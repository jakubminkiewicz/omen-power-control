// SPDX-License-Identifier: GPL-2.0-only
/* HP OMEN Transcend 14 GPU thermal-mode interface, as used by hp-wmi.c. */
#include <linux/acpi.h>
#include <linux/dmi.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/power_supply.h>
#include <linux/slab.h>
#include <linux/unaligned.h>

#define HP_BIOS_GUID "5FB7F034-2C63-45E9-BE91-3D44E2C707E4"

struct gpu_modes {
	u8 ctgp, ppab, dstate, slowdown;
};

static DEFINE_MUTEX(mode_lock);
static bool ready;
static bool owns_ctgp;

static int gpu_query(bool write, struct gpu_modes *modes)
{
	/* HP requires a 128-byte input area and a separate payload length. */
	struct {
		u32 signature, command, type, size;
		u8 data[128];
	} request = { 0x55434553, 0x20008, write ? 0x22 : 0x21, sizeof(*modes), {} };
	struct acpi_buffer input = { sizeof(request), &request };
	struct acpi_buffer output = { ACPI_ALLOCATE_BUFFER, NULL };
	union acpi_object *obj;
	acpi_status status;
	int err = -EIO;

	if (write)
		memcpy(request.data, modes, sizeof(*modes));
	status = wmi_evaluate_method(HP_BIOS_GUID, 0, write ? 1 : 2, &input, &output);
	obj = output.pointer;
	if (ACPI_FAILURE(status) || !obj || obj->type != ACPI_TYPE_BUFFER ||
	    !obj->buffer.pointer || obj->buffer.length < 8 + (write ? 0 : sizeof(*modes)))
		goto out;
	if (get_unaligned_le32(obj->buffer.pointer + 4))
		goto out;
	if (!write)
		memcpy(modes, obj->buffer.pointer + 8, sizeof(*modes));
	err = 0;
out:
	kfree(output.pointer);
	return err;
}

/* With mode_lock held. A release only reverts a flag this module enabled. */
static int apply_ctgp(bool enable)
{
	struct gpu_modes modes, readback;
	int err;

	if (!enable && !owns_ctgp)
		return 0;
	if (enable && power_supply_is_system_supplied() <= 0)
		return -EAGAIN;
	err = gpu_query(false, &modes);
	if (err)
		return err;
	if (modes.ctgp > 1 || modes.ppab > 1 || modes.dstate > 5 ||
	    modes.slowdown < 60 || modes.slowdown > 100)
		return -ERANGE;
	if (modes.ctgp != enable) {
		/* Record ownership before writing in case the readback fails. */
		if (enable)
			owns_ctgp = true;
		modes.ctgp = enable;
		err = gpu_query(true, &modes);
		if (err)
			return err;
		err = gpu_query(false, &readback);
		if (err)
			return err;
		if (memcmp(&readback, &modes, sizeof(modes)))
			return -EIO;
	}
	if (!enable)
		owns_ctgp = false;
	return 0;
}

static int request_set(const char *value, const struct kernel_param *param)
{
	bool enable;
	int err = kstrtobool(value, &enable);

	if (err)
		return err;
	mutex_lock(&mode_lock);
	err = ready ? apply_ctgp(enable) : -ENODEV;
	mutex_unlock(&mode_lock);
	return err;
}

static int request_get(char *buffer, const struct kernel_param *param)
{
	struct gpu_modes modes;
	int err;

	mutex_lock(&mode_lock);
	err = ready ? gpu_query(false, &modes) : -ENODEV;
	mutex_unlock(&mode_lock);
	if (err)
		return err;
	return sysfs_emit(buffer, "%u\n", modes.ctgp);
}

static const struct kernel_param_ops request_ops = {
	.set = request_set,
	.get = request_get,
};
module_param_cb(ctgp, &request_ops, NULL, 0644);
MODULE_PARM_DESC(ctgp, "Read the live firmware flag. Write 1 to request cTGP on AC, 0 to release it.");

static int __init omen_ctgp_init(void)
{
	if (!dmi_match(DMI_SYS_VENDOR, "HP") || !dmi_match(DMI_BOARD_NAME, "8C58") ||
	    !dmi_match(DMI_PRODUCT_SKU, "9R293EA#ABU"))
		return -ENODEV;
	mutex_lock(&mode_lock);
	ready = true;
	mutex_unlock(&mode_lock);
	return 0;
}

static void __exit omen_ctgp_exit(void)
{
	mutex_lock(&mode_lock);
	ready = false;
	if (apply_ctgp(false))
		pr_err("omen_ctgp: firmware did not confirm cTGP release on unload\n");
	mutex_unlock(&mode_lock);
}

module_init(omen_ctgp_init);
module_exit(omen_ctgp_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("AC Performance cTGP control for the HP OMEN Transcend 14 (8C58)");
