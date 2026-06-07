frappe.ui.form.on("Fio Settings", {
    refresh(frm) {
        frm.add_custom_button(
            __("Sync Now"),
            () => _call_with_alert(frm, "erpnext_banking.api.trigger_sync_fio", {}),
            __("Fio")
        );
        frm.add_custom_button(
            __("Dry-run"),
            () => _call_with_alert(frm, "erpnext_banking.api.trigger_sync_fio", {dry_run: 1}),
            __("Fio")
        );
        frm.add_custom_button(
            __("Lookback"),
            () => _call_with_alert(frm, "erpnext_banking.api.trigger_lookback_fio", {}),
            __("Fio")
        );
        frm.add_custom_button(
            __("Run Reconciliation"),
            () => _call_with_alert(frm, "erpnext_banking.api.trigger_reconcile_fio", {}),
            __("Fio")
        );
    },
});

function _call_with_alert(frm, method, args) {
    frappe.call({
        method: method,
        args: args,
        freeze: true,
        freeze_message: __("Running, this may take a few seconds…"),
        callback: ({message}) => {
            if (!message) return;
            const ind = (message.status === "Success")  ? "green"
                       : (message.status === "Partial") ? "orange"
                       : (message.status === "Error")   ? "red"
                       :                                  "blue";
            const lines = [];
            if (message.fetched !== undefined)   lines.push(`fetched=${message.fetched}`);
            if (message.created !== undefined)   lines.push(`created=${message.created}`);
            if (message.skipped_duplicates !== undefined) lines.push(`skipped=${message.skipped_duplicates}`);
            if (message.matched !== undefined)   lines.push(`matched=${message.matched}`);
            if (message.retried !== undefined && message.retried > 0)
                lines.push(`retried=${message.retried_succeeded}/${message.retried}`);
            const summary = lines.join(", ");
            const logLink = message.log_name
                ? ` · <a href="/app/bank-sync-log/${message.log_name}">${message.log_name}</a>`
                : "";
            frappe.show_alert({
                message: `${message.status || "Done"}: ${summary}${logLink}`,
                indicator: ind,
            }, 10);
            frm.reload_doc();
        },
        error: (err) => {
            frappe.show_alert({
                message: __("Failed: {0}", [(err && err.message) || "see browser console"]),
                indicator: "red",
            }, 10);
        },
    });
}
