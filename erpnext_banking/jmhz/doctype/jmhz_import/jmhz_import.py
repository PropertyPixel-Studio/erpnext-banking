"""Import of one JMHZ report (one file, one period)."""

from frappe.model.document import Document
from frappe.model.naming import make_autoname


class JMHZImport(Document):
	def autoname(self):
		# named by the reported period, not by the upload date
		self.name = make_autoname(f"JMHZ-{int(self.rok):04d}-{int(self.mesic):02d}-.##", doc=self)
