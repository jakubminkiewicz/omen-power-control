// SPDX-License-Identifier: GPL-2.0-only
/* Two-fan speed requests for the HP OMEN Transcend 14, board 8C58, BIOS F.13. */
#include <linux/acpi.h>
#include <linux/dmi.h>
#include <linux/jiffies.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/slab.h>
#include <linux/suspend.h>
#include <linux/unaligned.h>
#include <linux/workqueue.h>

#define HP_BIOS_GUID "5FB7F034-2C63-45E9-BE91-3D44E2C707E4"
#define EC_FIELD(name) "\\_SB.PC00.LPCB.EC0." name
#define FAN_COUNT 0x10
#define FAN_MAX 0x27
#define FAN_LEVEL 0x2e

/* F.13's fan table has targets of 19–57 and 19–55 in units of 100 RPM. */
#define FAN_MIN_PERCENT 35
#define FAN1_MAX_LEVEL 57
#define FAN2_MAX_LEVEL 55
#define FAN_LEASE (12 * HZ)
#define FAN_REFRESH (25 * HZ)

static DEFINE_MUTEX(fan_lock);
static bool ready, owned, sleeping;
static unsigned int percent;
static unsigned long expires, refresh;
static void lease_expired(struct work_struct *work);
static DECLARE_DELAYED_WORK(lease_work, lease_expired);

static int fan_query(u8 type, const u8 *value, size_t size, u8 *reply)
{
	struct {
		u32 signature, command, type, size;
		u8 data[128];
	} request = { 0x55434553, 0x20008, type, size, {} };
	struct acpi_buffer input = { sizeof(request), &request };
	struct acpi_buffer output = { ACPI_ALLOCATE_BUFFER, NULL };
	union acpi_object *obj;
	acpi_status status;
	int ret = -EIO;

	memcpy(request.data, value, size);
	status = wmi_evaluate_method(HP_BIOS_GUID, 0, reply ? 2 : 1, &input, &output);
	obj = output.pointer;
	if (ACPI_SUCCESS(status) && obj && obj->type == ACPI_TYPE_BUFFER &&
	    obj->buffer.pointer && obj->buffer.length >= 8 + (reply ? 1 : 0) &&
	    !get_unaligned_le32(obj->buffer.pointer + 4)) {
		if (reply)
			*reply = obj->buffer.pointer[8];
		ret = 0;
	}
	kfree(obj);
	return ret;
}

static int max_fans(bool enabled)
{
	const u8 value[4] = { enabled };

	return fan_query(FAN_MAX, value, sizeof(value), NULL);
}

static bool ec_value(const char *field, u8 expected)
{
	unsigned long long value;

	return ACPI_SUCCESS(acpi_evaluate_integer(NULL, (char *)field, NULL, &value)) &&
		value == expected;
}

static int release_fans(void)
{
	const u8 automatic[2] = {};
	int ret;

	if (!owned)
		return 0;
	ret = fan_query(FAN_LEVEL, automatic, sizeof(automatic), NULL);
	if (!ret)
		ret = max_fans(false);
	if (ret) {
		/* A failed handoff must not leave a low fixed speed running. */
		max_fans(true);
		pr_warn_ratelimited("omen_fan: firmware handoff failed, retrying\n");
		expires = jiffies;
		mod_delayed_work(system_freezable_wq, &lease_work, HZ);
		return ret;
	}
	percent = 0;
	owned = false;
	cancel_delayed_work(&lease_work);
	module_put(THIS_MODULE);
	return 0;
}

static void lease_expired(struct work_struct *work)
{
	unsigned long now;

	mutex_lock(&fan_lock);
	now = jiffies;
	if (owned) {
		if (time_before(now, expires))
			mod_delayed_work(system_freezable_wq, &lease_work, expires - now);
		else
			release_fans();
	}
	mutex_unlock(&fan_lock);
}

static int request_speed(unsigned int requested)
{
	const u8 zero = 0;
	const u8 targets[] = {
		DIV_ROUND_CLOSEST(requested * FAN1_MAX_LEVEL, 100),
		DIV_ROUND_CLOSEST(requested * FAN2_MAX_LEVEL, 100),
	};
	bool max = requested == 100;
	u8 count;
	int ret;

	if (owned && requested == percent && time_before(jiffies, refresh) &&
	    ec_value(EC_FIELD("FFFS"), max) &&
	    (max || (ec_value(EC_FIELD("SRP1"), targets[0]) &&
		     ec_value(EC_FIELD("SRP2"), targets[1]))))
		goto renew;

	if (!owned) {
		if (!try_module_get(THIS_MODULE))
			return -ENODEV;
		owned = true;
	}
	/* On F.13 this call also activates the EC's manual fan latch. */
	ret = fan_query(FAN_COUNT, &zero, sizeof(zero), &count);
	if (!ret && count != ARRAY_SIZE(targets))
		ret = -ENODEV;
	if (!ret && !max)
		ret = fan_query(FAN_LEVEL, targets, sizeof(targets), NULL);
	if (!ret)
		ret = max_fans(max);
	if (!ret && (!ec_value(EC_FIELD("FFFS"), max) ||
		     (!max && (!ec_value(EC_FIELD("SRP1"), targets[0]) ||
			       !ec_value(EC_FIELD("SRP2"), targets[1])))))
		ret = -EIO;
	if (ret) {
		release_fans();
		return ret;
	}
	percent = requested;
	refresh = jiffies + FAN_REFRESH;
renew:
	expires = jiffies + FAN_LEASE;
	mod_delayed_work(system_freezable_wq, &lease_work, FAN_LEASE);
	return 0;
}

static int percent_set(const char *value, const struct kernel_param *kp)
{
	unsigned int requested;
	int ret = kstrtouint(value, 10, &requested);

	if (ret)
		return ret;
	if (requested && (requested < FAN_MIN_PERCENT || requested > 100))
		return -ERANGE;
	mutex_lock(&fan_lock);
	if (!ready)
		ret = -ENODEV;
	else if (!requested)
		ret = release_fans();
	else if (sleeping)
		ret = -EBUSY;
	else
		ret = request_speed(requested);
	mutex_unlock(&fan_lock);
	return ret;
}

static int percent_get(char *buffer, const struct kernel_param *kp)
{
	return sysfs_emit(buffer, "%u\n", READ_ONCE(percent));
}

static const struct kernel_param_ops percent_ops = {
	.set = percent_set,
	.get = percent_get,
};
module_param_cb(fan_percent, &percent_ops, NULL, 0644);
MODULE_PARM_DESC(fan_percent, "Both fan targets at 35–100 percent, or 0 to return to firmware automatic");

static int fan_pm(struct notifier_block *nb, unsigned long event, void *unused)
{
	mutex_lock(&fan_lock);
	switch (event) {
	case PM_SUSPEND_PREPARE:
	case PM_HIBERNATION_PREPARE:
		sleeping = true;
		release_fans();
		break;
	case PM_POST_SUSPEND:
	case PM_POST_HIBERNATION:
		sleeping = false;
		break;
	}
	mutex_unlock(&fan_lock);
	return NOTIFY_OK;
}

static struct notifier_block fan_pm_notifier = { .notifier_call = fan_pm };

static int __init omen_fan_init(void)
{
	int ret;

	if (!dmi_match(DMI_SYS_VENDOR, "HP") ||
	    !dmi_match(DMI_BOARD_NAME, "8C58") ||
	    !dmi_match(DMI_PRODUCT_SKU, "9R293EA#ABU") ||
	    !dmi_match(DMI_BIOS_VERSION, "F.13"))
		return -ENODEV;
	ret = register_pm_notifier(&fan_pm_notifier);
	if (!ret)
		ready = true;
	return ret;
}

static void __exit omen_fan_exit(void)
{
	unregister_pm_notifier(&fan_pm_notifier);
	cancel_delayed_work_sync(&lease_work);
}

module_init(omen_fan_init);
module_exit(omen_fan_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Leased manual fan speed for HP OMEN Transcend 14 board 8C58 BIOS F.13");
