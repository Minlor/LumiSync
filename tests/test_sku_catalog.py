import unittest

from lumisync import connection, sku_catalog


SAMPLE_CACHE = """[LocalSku]
Sku=[{"sku":"H619C","name":"10m RGBIC Pro Strip Lights","segmentNums":10,"supportRazer":true,"supportColor":1,"supportMusicalFeast":1,"colorTemperatureStart":2000,"colorTemperatureEnd":9000},{"sku":"H61A0","name":"Neon Rope","segmentNums":15,"supportRazer":true}]
[Razer]
RazerList=[]
"""


class BuiltinCatalogTests(unittest.TestCase):
    def test_known_sku_reports_segment_count(self):
        self.assertEqual(sku_catalog.segment_count_for("H619C"), 10)

    def test_h6672_reports_community_confirmed_segment_count(self):
        self.assertEqual(sku_catalog.segment_count_for("H6672"), 14)

    def test_lookup_is_case_insensitive(self):
        self.assertEqual(sku_catalog.segment_count_for("h619c"), 10)

    def test_unknown_sku_returns_none(self):
        self.assertIsNone(sku_catalog.segment_count_for("H0000"))
        self.assertIsNone(sku_catalog.segment_count_for(None))

    def test_capabilities_expose_color_temp_range(self):
        cap = sku_catalog.capabilities_for("H619C")
        self.assertIsNotNone(cap)
        self.assertEqual((cap.color_temp_min, cap.color_temp_max), (2000, 9000))


class GetSegmentCountIntegrationTests(unittest.TestCase):
    def test_override_beats_catalog(self):
        self.assertEqual(
            connection.get_segment_count({"model": "H619C", "segment_count_override": 4}),
            4,
        )

    def test_catalog_supplies_count_for_known_model(self):
        self.assertEqual(connection.get_segment_count({"model": "H619C"}), 10)

    def test_unknown_model_uses_default(self):
        self.assertEqual(connection.get_segment_count({"model": "H0000"}, default=7), 7)


class GoveeCacheImportTests(unittest.TestCase):
    def test_parse_reads_sku_segments_and_caps(self):
        parsed = sku_catalog.parse_govee_cache(SAMPLE_CACHE)
        self.assertEqual(parsed["H619C"].segment_count, 10)
        self.assertEqual(parsed["H61A0"].segment_count, 15)
        self.assertEqual(parsed["H619C"].color_temp_max, 9000)

    def test_import_missing_file_is_noop(self):
        self.assertEqual(
            sku_catalog.import_govee_desktop_cache(r"C:\definitely\not\here.ini"), 0
        )

    def test_imported_entry_registers_and_overrides(self):
        original = dict(sku_catalog._RUNTIME)
        try:
            self.assertIsNone(sku_catalog.segment_count_for("H61A0"))
            for cap in sku_catalog.parse_govee_cache(SAMPLE_CACHE).values():
                if cap.segment_count > 0:
                    sku_catalog.register(cap)
            self.assertEqual(sku_catalog.segment_count_for("H61A0"), 15)
        finally:
            sku_catalog._RUNTIME.clear()
            sku_catalog._RUNTIME.update(original)


if __name__ == "__main__":
    unittest.main()
