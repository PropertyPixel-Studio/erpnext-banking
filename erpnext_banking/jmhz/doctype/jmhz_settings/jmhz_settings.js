frappe.ui.form.on("JMHZ Settings", {
	refresh(frm) {
		if (!frm.doc.post_accrual || frm.is_dirty() || !frappe.user.has_role("HR Manager")) return;
		// Slips submitted while booking was off: show first what would be posted, then post.
		frm.add_custom_button(__("Doúčtovat odeslané pásky"), () => {
			const d = new frappe.ui.Dialog({
				title: __("Doúčtování odeslaných pásek"),
				size: "large",
				fields: [
					{
						fieldname: "from_date",
						fieldtype: "Date",
						label: __("Jen pásky s koncem období od"),
						description: __("Prázdné: všechny odeslané pásky z JMHZ."),
					},
					{ fieldname: "result", fieldtype: "HTML" },
				],
				primary_action_label: __("Náhled"),
				primary_action: () => run_backfill(d, true),
				secondary_action_label: __("Zaúčtovat"),
				secondary_action: () =>
					frappe.confirm(
						__("Zaúčtovat předpis mezd a úhrady u pásek podle náhledu? Vzniknou účetní zápisy."),
						() => run_backfill(d, false)
					),
			});
			d.get_secondary_btn().hide(); // only after a preview
			d.show();
		});
	},
});

function run_backfill(d, dry_run) {
	return frappe
		.call({
			method: "erpnext_banking.jmhz.api.accrual_backfill",
			args: { from_date: d.get_value("from_date") || null, dry_run: dry_run ? 1 : 0 },
			freeze: true,
			freeze_message: dry_run ? __("Počítám náhled…") : __("Účtuji…"),
		})
		.then((r) => {
			const res = r.message || {};
			d.fields_dict.result.$wrapper.html(backfill_html(res));
			// posting is offered only when the preview has something to post
			const todo = (res.periods || []).some((p) => p.accrual_new || p.payment_booked || p.payment_moved);
			d.get_secondary_btn().toggle(!!res.dry_run && todo);
		});
}

function backfill_html(res) {
	const esc = frappe.utils.escape_html;
	const money = (v) => format_currency(v, "CZK", 0);
	const head = res.dry_run
		? `<div class="alert alert-info">${__("Náhled: nic se nezaúčtovalo.")}</div>`
		: `<div class="alert alert-success">${__("Zaúčtováno.")}</div>`;
	const bank = res.bank
		? ""
		: `<div class="alert alert-warning">${__("Bankovní napojení není zapnuté, platby se nehledají.")}</div>`;
	const periods = (res.periods || [])
		.map(
			(p) =>
				`<tr><td>${esc(p.period)}</td><td>${p.slips}</td><td>${p.accrual_new}</td><td>${p.accrual_existing}</td>` +
				`<td>${p.payment_booked}</td><td>${p.payment_moved}</td><td>${p.payment_linked}</td><td>${p.payment_none}</td>` +
				`<td>${p.errors ? `<b class="text-danger">${p.errors}</b>` : 0}</td></tr>`
		)
		.join("");
	const accounts = (res.accounts || [])
		.map((a) => `<tr><td>${esc(a.account)}</td><td class="text-right">${money(a.debit)}</td><td class="text-right">${money(a.credit)}</td></tr>`)
		.join("");
	const errors = (res.errors || [])
		.map((e) => `<tr><td>${esc(e.period)}</td><td>${esc(e.slip)}</td><td>${esc(e.message)}</td></tr>`)
		.join("");
	return `${head}${bank}
		<table class="table table-bordered table-condensed">
			<thead><tr><th>${__("Období")}</th><th>${__("Pásek")}</th><th>${__("Nový předpis")}</th><th>${__("Už zaúčtováno")}</th>
			<th>${__("Platba: uhradí závazek")}</th><th>${__("Platba: přeúčtuje se z nákladů")}</th><th>${__("Platba: v pořádku")}</th><th>${__("Bez platby")}</th><th>${__("Chyby")}</th></tr></thead>
			<tbody>${periods || `<tr><td colspan="9">${__("Žádné odeslané pásky.")}</td></tr>`}</tbody>
		</table>
		${accounts ? `<h6>${res.dry_run ? __("Obraty zápisů, které vzniknou") : __("Obraty zápisů, které vznikly")}</h6><table class="table table-bordered table-condensed"><thead><tr><th>${__("Účet")}</th><th class="text-right">${__("MD")}</th><th class="text-right">${__("D")}</th></tr></thead><tbody>${accounts}</tbody></table>` : ""}
		${errors ? `<h6 class="text-danger">${__("Pásky, které nejdou zaúčtovat")}</h6><table class="table table-bordered table-condensed"><thead><tr><th>${__("Období")}</th><th>${__("Páska")}</th><th>${__("Důvod")}</th></tr></thead><tbody>${errors}</tbody></table>` : ""}`;
}
