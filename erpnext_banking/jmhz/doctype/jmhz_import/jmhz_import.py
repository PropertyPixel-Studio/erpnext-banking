"""Import of one JMHZ report (one file, one period)."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname

from erpnext_banking.jmhz import importer


class JMHZImport(Document):
	def autoname(self):
		# named by the reported period, not by the upload date
		self.name = make_autoname(f"JMHZ-{int(self.rok):04d}-{int(self.mesic):02d}-.##", doc=self)

	def validate(self):
		if self.is_new() and not self.flags.jmhz_internal:
			# rows and amounts come only from a parsed file (upload of the XML)
			frappe.throw(_("Import JMHZ vzniká jen nahráním XML souboru."))
		if self.status == importer.NAHLED:
			if self.typ_podani != "S":  # a cancelling report has no rows
				importer.validate_pairing(self)
				importer.refresh_rows(self)
		elif not self.flags.jmhz_internal:
			# after leaving the preview the import is read-only for users
			frappe.throw(_("Import ve stavu {0} už nejde upravovat.").format(self.status))

	def on_trash(self):
		if self.status not in (importer.NAHLED, importer.ZRUSENO):
			frappe.throw(_("Smazat jde jen import ve stavu Náhled nebo Zrušeno."))

	@frappe.whitelist()
	def can_create_slips(self) -> bool:
		return importer.can_create_slips(self)
