frappe.ui.form.on("JMHZ Import", {
	refresh(frm) {
		const preview = frm.doc.status === "Náhled";
		frm.set_read_only(!preview);
		frm.set_df_property("replace_confirmed", "read_only", !preview);
		frm.fields_dict.rows.grid.update_docfield_property("employee", "read_only", !preview);
		frm.fields_dict.rows.grid.update_docfield_property("skip", "read_only", !preview);
		frm.fields_dict.rows.grid.update_docfield_property("skip_reason", "read_only", !preview);
		frm.fields_dict.rows.grid.update_docfield_property("rucni_srazka", "read_only", !preview);
		frm.fields_dict.rows.grid.update_docfield_property("rucni_srazka_poznamka", "read_only", !preview);
		frm.fields_dict.rows.grid.cannot_add_rows = true;
		frm.fields_dict.rows.grid.cannot_delete_rows = true;

		if (!frappe.user.has_role("HR Manager") || frm.is_new()) return;

		if (preview && frm.doc.typ_podani === "S") {
			frm.add_custom_button(__("Provést storno"), () => {
				frappe.confirm(
					__("Zrušit všechny pásky a Additional Salary z JMHZ za toto období?"),
					() => call_import(frm, "run_storno", __("Ruším pásky…"))
				);
			}).addClass("btn-danger");
		} else if (preview && !frm.is_dirty()) {
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
		if (
			frm.doc.typ_podani !== "S" &&
			["Náhled", "Pásky vytvořeny", "Dokončeno"].includes(frm.doc.status)
		) {
			frm.add_custom_button(
				__("Zrušit import"),
				() => {
					frappe.confirm(
						__("Zrušit všechny pásky a Additional Salary tohoto importu?"),
						() => call_import(frm, "cancel_import", __("Ruším import…"))
					);
				},
				__("Další")
			);
		}
		if (["Náhled", "Pásky vytvořeny"].includes(frm.doc.status) && frm.doc.typ_podani !== "S") {
			add_pdf_buttons(frm);
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

function add_pdf_buttons(frm) {
	const group = __("PDF pásky");
	frm.add_custom_button(
		__("Nahrát PDF pásky"),
		() => {
			// The summary PDF goes to a whitelisted method only, only the per-person cuts are stored.
			new frappe.ui.FileUploader({
				method: "erpnext_banking.jmhz.api.upload_pdf",
				doctype: "JMHZ Import",
				docname: frm.doc.name,
				restrictions: { allowed_file_types: [".pdf"] },
				allow_multiple: false,
				make_attachments_public: false,
				allow_web_link: false,
				allow_take_photo: false,
				allow_google_drive: false,
				on_success(_file, r) {
					const res = (r && r.message) || {};
					const lines = [__("Pásek v PDF: {0}, přiřazeno: {1}", [res.slips, res.assigned])];
					if ((res.unassigned || []).length)
						lines.push(__("Nepřiřazeno: {0}", [res.unassigned.join("; ")]));
					if ((res.mismatch || []).length)
						lines.push(__("Neshoda s JMHZ: {0}", [res.mismatch.join(", ")]));
					frappe.msgprint(lines.join("<br>"));
					frm.reload_doc();
				},
			});
		},
		group
	);

	const pending = JSON.parse(frm.doc.pdf_unassigned || "[]");
	if (pending.length) {
		frm.add_custom_button(
			__("Přiřadit PDF ručně"),
			() => {
				const rows = (frm.doc.rows || []).filter(
					(r) => !["Bez příjmu", "Zrušeno"].includes(r.status)
				);
				const d = new frappe.ui.Dialog({
					title: __("Přiřadit PDF pásku"),
					fields: [
						{
							fieldname: "file_url",
							fieldtype: "Select",
							label: __("Páska z PDF"),
							reqd: 1,
							options: pending.map((p) => ({ value: p.file_url, label: p.label })),
						},
						{
							fieldname: "row",
							fieldtype: "Select",
							label: __("Osoba"),
							reqd: 1,
							options: rows.map((r) => ({
								value: r.name,
								label: [r.pseudonym, r.employee_name].filter(Boolean).join(" – "),
							})),
						},
					],
					primary_action_label: __("Přiřadit"),
					primary_action(values) {
						frappe
							.call({
								method: "erpnext_banking.jmhz.api.assign_pdf",
								args: { name: frm.doc.name, file_url: values.file_url, row: values.row },
							})
							.then(() => {
								d.hide();
								frm.reload_doc();
							});
					},
				});
				d.show();
			},
			group
		);
	}

	if ((frm.doc.rows || []).some((r) => r.pdf_check === "Neshoda")) {
		frm.add_custom_button(
			__("Potvrdit neshody PDF"),
			() => {
				const names = frm.doc.rows
					.filter((r) => r.pdf_check === "Neshoda")
					.map((r) => `${r.pseudonym}: ${r.pdf_check_detail || ""}`);
				frappe.confirm(
					__("PDF páska nesedí s JMHZ u těchto osob. Potvrzujete, že je to v pořádku?") +
						"<br><br>" +
						names.join("<br>"),
					() => call_import(frm, "confirm_pdf", __("Potvrzuji…"))
				);
			},
			group
		);
	}

	if ((frm.doc.rows || []).some((r) => r.slip_pdf) || pending.length) {
		frm.add_custom_button(
			__("Smazat PDF"),
			() => {
				frappe.confirm(__("Smazat všechny výřezy PDF pásek tohoto importu?"), () =>
					call_import(frm, "delete_pdfs", __("Mažu PDF…"))
				);
			},
			group
		);
	}
}
