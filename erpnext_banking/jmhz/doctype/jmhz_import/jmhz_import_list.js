frappe.listview_settings["JMHZ Import"] = {
	onload(listview) {
		if (!frappe.user.has_role("HR Manager")) return;
		listview.page.add_inner_button(__("Nahrát JMHZ XML"), () => {
			// The file goes to a whitelisted method only. Frappe does not store it as a File.
			new frappe.ui.FileUploader({
				method: "erpnext_banking.jmhz.api.upload_xml",
				restrictions: { allowed_file_types: [".xml"] },
				allow_multiple: false,
				make_attachments_public: false,
				allow_web_link: false,
				allow_take_photo: false,
				allow_google_drive: false,
				on_success(_file, r) {
					const name = r && r.message && r.message.name;
					if (name) frappe.set_route("Form", "JMHZ Import", name);
				},
			});
		});
	},
	get_indicator(doc) {
		const colors = {
			"Náhled": "orange",
			"Pásky vytvořeny": "blue",
			"Dokončeno": "green",
			"Zrušeno": "red",
			"Nahrazeno": "gray",
		};
		return [__(doc.status), colors[doc.status] || "gray", "status,=," + doc.status];
	},
};
