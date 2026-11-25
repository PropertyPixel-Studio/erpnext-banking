// JMHZ module: send a submitted slip by e-mail according to JMHZ Settings (e-mail mode).
frappe.ui.form.on("Salary Slip", {
	refresh(frm) {
		if (frm.doc.docstatus !== 1 || !frappe.user.has_role("HR Manager")) return;
		frm.add_custom_button(__("Poslat pásku e-mailem"), () => {
			frappe
				.call({
					method: "erpnext_banking.jmhz.mailer.send_slip",
					args: { name: frm.doc.name },
					freeze: true,
				})
				.then((r) => {
					if (r.message) {
						frappe.show_alert({ message: __("Páska je zařazená k odeslání"), indicator: "green" });
					}
				});
		});
	},
});
