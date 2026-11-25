frappe.ui.form.on("JMHZ Import", {
	refresh(frm) {
		const preview = frm.doc.status === "Náhled";
		frm.set_read_only(!preview);
		frm.fields_dict.rows.grid.update_docfield_property("employee", "read_only", !preview);
		frm.fields_dict.rows.grid.update_docfield_property("skip", "read_only", !preview);
		frm.fields_dict.rows.grid.update_docfield_property("skip_reason", "read_only", !preview);
		frm.fields_dict.rows.grid.update_docfield_property("rucni_srazka", "read_only", !preview);
		frm.fields_dict.rows.grid.update_docfield_property("rucni_srazka_poznamka", "read_only", !preview);
		frm.fields_dict.rows.grid.cannot_add_rows = true;
		frm.fields_dict.rows.grid.cannot_delete_rows = true;

		if (!frappe.user.has_role("HR Manager") || frm.is_new()) return;

		if (preview && !frm.is_dirty()) {
			frm.add_custom_button(__("Vytvořit pásky"), () => {
				frm.call("can_create_slips").then((r) => {
					if (!r.message) {
						frappe.msgprint(
							__("Všechny osoby musí být připravené, přeskočené nebo bez příjmu.")
						);
						return;
					}
					call_import(frm, "create_slips", __("Vytvářím pásky…"));
				});
			}).addClass("btn-primary");
		}
		if (frm.doc.status === "Pásky vytvořeny") {
			frm.add_custom_button(__("Odeslat pásky"), () => {
				frappe.confirm(
					__("Odeslat všechny pásky importu? Zaměstnanci je potom uvidí."),
					() => call_import(frm, "submit_slips", __("Odesílám pásky…"))
				);
			}).addClass("btn-primary");
		}
	},
});

function call_import(frm, method, message) {
	frappe
		.call({
			method: "erpnext_banking.jmhz.api." + method,
			args: { name: frm.doc.name },
			freeze: true,
			freeze_message: message,
		})
		.then((r) => {
			const res = r.message || {};
			if (res.ok === false) frappe.msgprint({ title: __("Nic se neuložilo"), message: res.message, indicator: "red" });
			frm.reload_doc();
		});
}
