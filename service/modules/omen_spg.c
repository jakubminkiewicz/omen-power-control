// SPDX-License-Identifier: GPL-2.0-only
/* HP OMEN 8C58 F.13 concurrent CPU/GPU power budget. */
#include <linux/acpi.h>
#include <linux/dmi.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/power_supply.h>
#include <linux/slab.h>
#include <linux/suspend.h>
#include <linux/unaligned.h>
#include <linux/workqueue.h>

#define HP_BIOS_GUID "5FB7F034-2C63-45E9-BE91-3D44E2C707E4"
#define BASE_BUDGET_WATTS 30
#define GAIN_STEP_WATTS 5
#define MAX_GAIN_WATTS 30
#define MIRROR_INDEX 0x2a

static DEFINE_MUTEX(lock);
static bool ready, sleeping, owned, waiting_for_performance;
static u8 original_mirror;
static u64 owned_raw;
static unsigned long expires;
static void release_work(struct work_struct *work);
static DECLARE_DELAYED_WORK(cleanup, release_work);

/* HP gaming WMI: 0x28 capabilities, 0x29 setter, 0x2a live limits. */
static int hp_call(u8 command, u8 watts, u8 *out, size_t len)
{
	bool write = command == 0x29;
	struct {
		u32 signature, command, type, size;
		u8 data[128];
	} req = { 0x55434553, 0x20008, command, write ? 4 : 0, {} };
	struct acpi_buffer input = { sizeof(req), &req };
	struct acpi_buffer output = { ACPI_ALLOCATE_BUFFER, NULL };
	union acpi_object *obj;
	acpi_status status;
	int ret = -EIO;

	if (write) {
		memset(req.data, 0xff, 3); /* Leave the three CPU limits alone. */
		req.data[3] = watts;
	}
	status = wmi_evaluate_method(HP_BIOS_GUID, 0, write ? 1 : 3, &input, &output);
	obj = output.pointer;
	if (ACPI_SUCCESS(status) && obj && obj->type == ACPI_TYPE_BUFFER &&
	    obj->buffer.pointer && obj->buffer.length >= 8 + len &&
	    !get_unaligned_le32(obj->buffer.pointer + 4)) {
		if (len)
			memcpy(out, obj->buffer.pointer + 8, len);
		ret = 0;
	}
	kfree(output.pointer);
	return ret;
}

static int request_raw(u64 *raw)
{
	return ACPI_FAILURE(acpi_evaluate_integer(NULL, "\\_SB.NPCF.DATP", NULL, raw)) ? -EIO : 0;
}

static int performance(void)
{
	u64 mode;

	if (ACPI_FAILURE(acpi_evaluate_integer(NULL, "\\_SB.PC00.LPCB.EC0.HPCM", NULL, &mode)))
		return -EIO;
	return mode == 0x11 || mode == 0x21 || mode == 0x31;
}

static int mirror(u64 *value, bool restore_original)
{
	union acpi_object v[] = {
		{ .integer = { .type = ACPI_TYPE_INTEGER, .value = MIRROR_INDEX } },
		{ .integer = { .type = ACPI_TYPE_INTEGER, .value = original_mirror } },
	};
	struct acpi_object_list args = { 1, v };

	if (restore_original) {
		args.count = 2;
		if (ACPI_FAILURE(acpi_evaluate_object(NULL, "\\CMSW", &args, NULL)))
			return -EIO;
		args.count = 1;
	}
	return ACPI_FAILURE(acpi_evaluate_integer(NULL, "\\CMSR", &args, value)) ? -EIO : 0;
}

static bool supply_online(const char *name)
{
	struct power_supply *supply = power_supply_get_by_name(name);
	union power_supply_propval value;
	bool online = false;

	if (supply) {
		online = !power_supply_get_property(supply, POWER_SUPPLY_PROP_ONLINE, &value)
			 && value.intval == 1;
		power_supply_put(supply);
	}
	return online;
}

static void disown(void)
{
	waiting_for_performance = false;
	owned = false;
	owned_raw = 0;
	module_put(THIS_MODULE);
}

/* GM29 also mirrors its request in CMOS. Keep that byte at its pre-service
 * value even while boosting; the active budget comes from DATP in RAM. */
static int restore_mirror(u64 saved)
{
	if (saved == original_mirror)
		return 0;
	return mirror(&saved, true) || saved != original_mirror ? -EIO : 0;
}

/* In Balanced/Cool firmware ignores DATP but refuses to change it. Clear
 * the dormant RAM request when GM29 next accepts writes. */
static int restore(void)
{
	u64 raw, saved;
	int perf, ret;

	if (!owned)
		return 0;
	if (request_raw(&raw) || mirror(&saved, false))
		return -EIO;
	if ((raw && raw != owned_raw) ||
	    (saved && saved != original_mirror && saved != (u8)owned_raw)) {
		disown(); /* Another writer has taken control. */
		return 0;
	}
	if (raw) {
		perf = performance();
		if (perf < 0)
			return perf;
		if (perf) {
			ret = hp_call(0x29, 0, NULL, 0);
			if (ret || request_raw(&raw) || mirror(&saved, false))
				return -EIO;
		}
	}
	if (restore_mirror(saved))
		return -EIO;
	if (raw) {
		waiting_for_performance = true;
		return -EAGAIN;
	}
	disown();
	return 0;
}

static void release_work(struct work_struct *work)
{
	unsigned long now;

	mutex_lock(&lock);
	now = jiffies;
	if (owned) {
		if (time_before(now, expires))
			mod_delayed_work(system_freezable_wq, &cleanup, expires - now);
		else if ((waiting_for_performance && performance() == 0) || restore())
			mod_delayed_work(system_freezable_wq, &cleanup, HZ);
	}
	mutex_unlock(&lock);
}

static int release_gain(void)
{
	int ret;

	expires = jiffies;
	ret = restore();
	if (ret)
		mod_delayed_work(system_freezable_wq, &cleanup, HZ);
	/* Outside Performance firmware already uses its stock value. */
	return ret == -EAGAIN ? 0 : ret;
}

static int enable_gain(unsigned int watts)
{
	u8 target = BASE_BUDGET_WATTS + watts;
	u64 target_raw = target * 8;
	u8 caps[9];
	u64 raw, saved;
	int ret;

	if (sleeping || !supply_online("ADP1") || !supply_online("ucsi-source-psy-USBC000:002") ||
	    performance() != 1)
		return -EAGAIN;
	ret = hp_call(0x28, 0, caps, sizeof(caps));
	if (ret)
		return ret;
	if (get_unaligned_le16(caps) != 140 || caps[8] != BASE_BUDGET_WATTS)
		return -EOPNOTSUPP;
	/* Finish releasing the previous level before taking ownership of a new one. */
	if (owned && owned_raw != target_raw) {
		ret = release_gain();
		if (ret || owned)
			return ret ?: -EAGAIN;
	}
	if (request_raw(&raw) || mirror(&saved, false))
		return -EIO;
	if ((raw && (!owned || raw != owned_raw)) ||
	    (owned && saved != original_mirror && saved != (u8)owned_raw))
		return -EBUSY;
	if (!owned) {
		if (!try_module_get(THIS_MODULE))
			return -ENODEV;
		original_mirror = saved;
		owned_raw = target_raw;
		owned = true; /* Keep cleanup available even if the service stops. */
	}
	waiting_for_performance = false;
	expires = jiffies + 100 * HZ; /* The service renews every 60 seconds. */
	mod_delayed_work(system_freezable_wq, &cleanup, 100 * HZ);
	if (raw == target_raw)
		return restore_mirror(saved);
	ret = hp_call(0x29, target, NULL, 0);
	if (ret || request_raw(&raw) || mirror(&saved, false) ||
	    raw != target_raw || saved != (u8)target_raw || restore_mirror(saved)) {
		release_gain();
		return ret ? ret : -EIO;
	}
	return 0;
}

static int gain_set(const char *value, const struct kernel_param *param)
{
	unsigned int watts;
	int ret = kstrtouint(value, 10, &watts);

	if (ret || watts > MAX_GAIN_WATTS || watts % GAIN_STEP_WATTS)
		return -EINVAL;
	mutex_lock(&lock);
	ret = !ready ? -ENODEV : watts ? enable_gain(watts) : release_gain();
	mutex_unlock(&lock);
	return ret;
}

static int gain_get(char *buf, const struct kernel_param *param)
{
	u8 limits[8];
	int ret;

	mutex_lock(&lock);
	ret = ready ? hp_call(0x2a, 0, limits, sizeof(limits)) : -ENODEV;
	mutex_unlock(&lock);
	if (ret)
		return ret;
	return sysfs_emit(buf, "%d\n", max((int)limits[7] - BASE_BUDGET_WATTS, 0));
}

static const struct kernel_param_ops gain_ops = { .set = gain_set, .get = gain_get };
module_param_cb(gain_watts, &gain_ops, NULL, 0644);
MODULE_PARM_DESC(gain_watts, "Effective Smart Performance Gain in watts. Write 5-30 in 5 W steps or 0 to release.");

static int pm_event(struct notifier_block *nb, unsigned long event, void *unused)
{
	mutex_lock(&lock);
	switch (event) {
	case PM_SUSPEND_PREPARE:
	case PM_HIBERNATION_PREPARE:
	case PM_RESTORE_PREPARE:
		sleeping = true;
		release_gain();
		break;
	case PM_POST_SUSPEND:
	case PM_POST_HIBERNATION:
	case PM_POST_RESTORE:
		sleeping = false;
		break;
	}
	mutex_unlock(&lock);
	return NOTIFY_OK;
}
static struct notifier_block pm = { .notifier_call = pm_event };

static int __init spg_init(void)
{
	int ret;

	if (!dmi_match(DMI_SYS_VENDOR, "HP") || !dmi_match(DMI_BOARD_NAME, "8C58") ||
	    !dmi_match(DMI_PRODUCT_SKU, "9R293EA#ABU") || !dmi_match(DMI_BIOS_VERSION, "F.13"))
		return -ENODEV;
	ret = register_pm_notifier(&pm);
	if (!ret)
		ready = true;
	return ret;
}

static void __exit spg_exit(void)
{
	unregister_pm_notifier(&pm);
	cancel_delayed_work_sync(&cleanup);
}
module_init(spg_init);
module_exit(spg_exit);
MODULE_LICENSE("GPL");
MODULE_VERSION("2");
MODULE_DESCRIPTION("Rear AC Performance Smart Performance Gain for the HP OMEN 8C58 F.13");
