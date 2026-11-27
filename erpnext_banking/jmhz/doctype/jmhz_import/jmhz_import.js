frappe.ui.form.on("JMHZ Import", {
	refresh(frm) {
		const preview = frm.doc.status === "Náhled";
		// frm.set_read_only() takes no argument and always locks the form: call it only outside the preview
		if (!preview) frm.set_read_only();
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
		if ((frm.doc.rows || []).some((r) => r.slip_pdf)) {
			// also after the slips are submitted, then read-only
			const todo = frm.doc.rows.filter((r) => r.slip_pdf && !r.pdf_reviewed).length;
			const editable = ["Náhled", "Pásky vytvořeny"].includes(frm.doc.status);
			const button = frm.add_custom_button(
				todo && editable ? __("Zkontrolovat PDF pásky ({0})", [todo]) : __("Zkontrolované PDF pásky"),
				() => open_pdf_review(frm)
			);
			if (todo && editable && frm.doc.status === "Pásky vytvořeny") button.addClass("btn-primary");
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
			// The report goes to a whitelisted method, which stores it and the per-person cuts
			// as private files of this import (HR Manager only).
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
						lines.push(__("Neshoda: {0}", [res.mismatch.join(", ")]));
					if ((res.unread || []).length)
						lines.push(__("Některá pole se nepřečetla: {0}", [res.unread.join(", ")]));
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
					__("PDF páska nesedí s JMHZ nebo sama se sebou u těchto osob. Potvrzujete, že je to v pořádku?") +
						"<br><br>" +
						names.join("<br>"),
					() => call_import(frm, "confirm_pdf", __("Potvrzuji…"))
				);
			},
			group
		);
	}

	if (frm.doc.report_pdf || (frm.doc.rows || []).some((r) => r.slip_pdf) || pending.length) {
		frm.add_custom_button(
			__("Smazat PDF"),
			() => {
				frappe.confirm(__("Smazat sestavu a všechny výřezy PDF pásek tohoto importu?"), () =>
					call_import(frm, "delete_pdfs", __("Mažu PDF…"))
				);
			},
			group
		);
	}
}


// --- review of the printed slips, person by person ---------------------------------
// The cut of the printed slip on the left, the values read from it on the right. Unread
// values are highlighted. Values outside the 12 control fields can be corrected; a
// correction cancels the confirmation. Nothing here is shown to employees.

function open_pdf_review(frm, row_name) {
	const rows = (frm.doc.rows || []).filter((r) => r.slip_pdf);
	if (!rows.length) return;
	const start = row_name || (rows.find((r) => !r.pdf_reviewed) || rows[0]).name;
	const d = new frappe.ui.Dialog({
		title: __("Kontrola PDF pásky"),
		size: "extra-large",
		fields: [
			{
				fieldname: "row",
				fieldtype: "Select",
				label: __("Osoba"),
				options: rows.map((r) => ({ value: r.name, label: review_row_label(r) })),
				default: start,
				onchange: () => load_pdf_review(frm, d),
			},
			{ fieldname: "body", fieldtype: "HTML" },
		],
		primary_action_label: __("Potvrdit kontrolu"),
		primary_action: () => confirm_pdf_review(frm, d),
		secondary_action_label: __("Uložit opravy"),
		secondary_action: () => save_pdf_corrections(frm, d).then(() => load_pdf_review(frm, d)),
	});
	d.onhide = () => frm.reload_doc();
	d.show();
	d.set_value("row", start).then(() => load_pdf_review(frm, d));
}

function review_row_label(r) {
	const name = [r.pseudonym, r.employee_name].filter(Boolean).join(" – ");
	return (r.pdf_reviewed ? "✓ " : "… ") + name;
}

function load_pdf_review(frm, d) {
	const row = d.get_value("row");
	if (!row) return Promise.resolve();
	return frappe
		.call({ method: "erpnext_banking.jmhz.api.pdf_review_data", args: { name: frm.doc.name, row } })
		.then((r) => render_pdf_review(d, r.message));
}

function render_pdf_review(d, data) {
	const esc = frappe.utils.escape_html;
	d.jmhz_review = data;
	const status = [];
	if (data.pdf_check === "Neshoda")
		status.push(
			`<div class="alert alert-danger">${__("Neshoda")}: ${esc(data.pdf_check_detail || "")}</div>`
		);
	else if (data.pdf_check === "Potvrzeno ručně")
		status.push(
			`<div class="alert alert-warning">${__("Neshoda potvrzená ručně")}: ${esc(
				data.pdf_check_detail || ""
			)}</div>`
		);
	const unread = data.blocks.reduce((n, b) => n + b.rows.filter((r) => r.unread).length, 0);
	if (unread)
		status.push(
			`<div class="alert alert-warning">${__(
				"Nepřečtená pole: {0}. Doplňte je podle výřezu, nebo je nechte prázdná, pokud na pásce nic není.",
				[unread]
			)}</div>`
		);
	if (data.reviewed)
		status.push(
			`<div class="alert alert-success">${__("Zkontroloval {0}, {1}", [
				esc(data.reviewed_by || ""),
				esc(frappe.datetime.str_to_user(data.reviewed_on) || ""),
			])}</div>`
		);

	const blocks = data.blocks
		.map((b) => {
			const rows = b.rows
				.map((r) => {
					const marks = [];
					if (r.unread) marks.push(`<span class="indicator-pill orange">${__("nepřečteno")}</span>`);
					if (r.corrected) marks.push(`<span class="indicator-pill blue">${__("opraveno ručně")}</span>`);
					const value =
						r.editable && data.editable
							? `<input type="text" class="form-control input-xs jmhz-review-input" data-key="${esc(
									r.key
							  )}" data-original="${esc(r.value)}" value="${esc(r.value)}" style="text-align:${
									r.kind === "text" ? "left" : "right"
							  }">`
							: `<div style="text-align:${r.kind === "text" ? "left" : "right"}">${esc(r.value)}${
									r.kind === "rate" && r.value ? " %" : ""
							  }</div>`;
					const background = r.unread ? "background: var(--bg-orange);" : "";
					return `<tr style="${background}"><td style="padding: 3px 8px; vertical-align: middle">${esc(
						r.label
					)} ${marks.join(
						" "
					)}</td><td style="width: 38%; padding: 3px 8px; vertical-align: middle">${value}</td></tr>`;
				})
				.join("");
			return `<h6 style="margin-top: 12px">${esc(b.title)}</h6><table class="table table-condensed" style="margin-bottom: 4px">${rows}</table>`;
		})
		.join("");

	// the cut is a private file of the import: only HR Manager can open it
	const html = `${status.join("")}
		<div class="row">
			<div class="col-md-7">
				<iframe src="${encodeURI(data.slip_pdf)}#toolbar=0&view=FitH" style="width: 100%; height: 72vh; border: 1px solid var(--border-color)"></iframe>
			</div>
			<div class="col-md-5" style="height: 72vh; overflow-y: auto">${blocks}</div>
		</div>`;
	d.fields_dict.body.$wrapper.html(html);
	// a confirmed slip can be opened again: the same button takes the confirmation back
	d.get_primary_btn()
		.text(data.reviewed ? __("Vzít potvrzení zpět") : __("Potvrdit kontrolu"))
		.prop("disabled", !data.editable);
	d.get_secondary_btn().toggle(!!data.editable);
}

function pdf_review_changes(d) {
	const values = {};
	d.fields_dict.body.$wrapper.find(".jmhz-review-input").each(function () {
		const input = $(this);
		if (input.val().trim() !== String(input.data("original"))) values[input.data("key")] = input.val();
	});
	return values;
}

function save_pdf_corrections(frm, d) {
	const values = pdf_review_changes(d);
	if (!Object.keys(values).length) return Promise.resolve();
	return frappe.call({
		method: "erpnext_banking.jmhz.api.correct_pdf",
		args: { name: frm.doc.name, row: d.get_value("row"), values },
		freeze: true,
	});
}

function confirm_pdf_review(frm, d) {
	if (d.jmhz_review && d.jmhz_review.reviewed) {
		return frappe
			.call({
				method: "erpnext_banking.jmhz.api.unreview_pdf",
				args: { name: frm.doc.name, row: d.get_value("row") },
				freeze: true,
			})
			.then(() => load_pdf_review(frm, d));
	}
	const send = (accept_mismatch) =>
		frappe
			.call({
				method: "erpnext_banking.jmhz.api.review_pdf",
				args: { name: frm.doc.name, row: d.get_value("row"), accept_mismatch: accept_mismatch ? 1 : 0 },
				freeze: true,
			})
			.then(() => next_pdf_review(frm, d));
	// corrections first: they are checked again on the server and may change the result
	save_pdf_corrections(frm, d)
		.then(() => load_pdf_review(frm, d))
		.then(() => {
			const data = d.jmhz_review;
			if (data.pdf_check !== "Neshoda") return send(false);
			frappe.confirm(
				__("PDF páska nesedí: {0}. Potvrzujete, že je to v pořádku?", [
					frappe.utils.escape_html(data.pdf_check_detail || ""),
				]),
				() => send(true)
			);
		});
}

function next_pdf_review(frm, d) {
	return frm.reload_doc().then(() => {
		const rows = (frm.doc.rows || []).filter((r) => r.slip_pdf);
		d.set_df_property(
			"row",
			"options",
			rows.map((r) => ({ value: r.name, label: review_row_label(r) }))
		);
		const next = rows.find((r) => !r.pdf_reviewed);
		if (!next) {
			d.onhide = null;
			d.hide();
			frappe.show_alert({ message: __("Všechny PDF pásky jsou zkontrolované."), indicator: "green" });
			return;
		}
		return d.set_value("row", next.name).then(() => load_pdf_review(frm, d));
	});
}
