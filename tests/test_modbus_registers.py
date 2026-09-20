import unittest

import modbus_registers as m


class TestCatalogSpec(unittest.TestCase):
    def test_length_and_uniqueness(self):
        items = m.all_items()
        self.assertEqual(len(items), 62)
        keys = [it["key"] for it in items]
        self.assertEqual(len(set(keys)), len(keys))
        by_reg = {}
        for it in items:
            by_reg.setdefault(it["reg"], []).append(it)
        for reg, group in by_reg.items():
            group = sorted(group, key=lambda it: it.get("bit0", 0) or 0)
            for i in range(len(group) - 1):
                a, b = group[i], group[i + 1]
                if a.get("bit0") is not None and a.get("bitwidth") is not None:
                    top = a["bit0"] + a["bitwidth"]
                    if b.get("bit0") is not None and top > b["bit0"]:
                        self.fail("overlapping bits on register %s" % reg)

    def test_categories(self):
        keys = [c["key"] for c in m.categories()]
        self.assertEqual(keys, ["beep", "application", "charge", "discharge", "battery"])

    def test_every_item_has_a_category(self):
        cat_keys = {c["key"] for c in m.categories()}
        for it in m.all_items():
            self.assertIn(it["category"], cat_keys)

    def test_kinds(self):
        kinds = {it["kind"] for it in m.all_items()}
        self.assertLessEqual(kinds, {"toggle", "select", "number", "time"})

    def test_danger_and_verify_sets(self):
        danger = sorted(it["key"] for it in m.all_items() if it.get("danger"))
        expected = [
            "battery_capacity",
            "cutoff_soc_eps",
            "cutoff_soc_grid",
            "cutoff_voltage_eps",
            "cutoff_voltage_grid",
            "eps_frequency",
            "eps_voltage",
            "grid_export_enable",
            "grid_export_percent",
            "hybrid",
        ]
        self.assertEqual(danger, expected)
        verify = [it["key"] for it in m.all_items() if it.get("verify")]
        self.assertEqual(verify, ["hybrid"])

    def test_select_options_are_valid(self):
        for it in m.all_items():
            if it["kind"] == "select":
                self.assertTrue(it.get("options"))
                values = [opt["value"] for opt in it["options"]]
                self.assertEqual(len(set(values)), len(values))

    def test_get_item_and_get_items(self):
        self.assertIsNotNone(m.get_item("buzzer"))
        self.assertIsNone(m.get_item("not_a_real_key"))
        charge_items = m.get_items("charge")
        self.assertTrue(all(it["category"] == "charge" for it in charge_items))
        self.assertGreater(len(charge_items), 0)


class TestExtract(unittest.TestCase):
    def test_toggle_bit(self):
        buzzer = m.get_item("buzzer")
        self.assertTrue(buzzer["bit"] == 7)
        self.assertEqual(m.extract_value(buzzer, 0x0080), 1)
        self.assertEqual(m.extract_value(buzzer, 0x007F), 0)

    def test_select_bitfield(self):
        ac_charge_type = m.get_item("ac_charge_type")
        self.assertEqual(m.extract_value(ac_charge_type, 0x0006), 3)  # bits 1-3 = 0b011
        self.assertEqual(m.extract_value(ac_charge_type, 0x0000), 0)

    def test_number_with_scale(self):
        item = m.get_item("battery_capacity") or m.get_item("nominal_voltage")
        if item["key"] == "battery_capacity":
            self.assertEqual(m.extract_value(item, 200), 200)
        else:
            self.assertEqual(m.extract_value(item, 465), 46.5)

    def test_number_high_byte(self):
        ac_power = m.get_item("ac_charge_power")
        self.assertTrue(ac_power["high_byte"])
        self.assertEqual(ac_power["reg"], 66)
        self.assertEqual(m.extract_value(ac_power, 0x6400), 100)
        self.assertEqual(m.extract_value(ac_power, 0x0064), 0)  # value lives in upper byte

        soc = m.get_item("charge_first_soc_limit")
        self.assertTrue(soc["high_byte"])
        self.assertEqual(soc["reg"], 75)
        self.assertEqual(m.extract_value(soc, 0x5500), 85)

    def test_other_percent_numbers_are_low_byte(self):
        self.assertFalse(m.get_item("ac_charge_soc_limit").get("high_byte"))
        self.assertFalse(m.get_item("forced_discharge_soc_limit").get("high_byte"))
        self.assertEqual(m.extract_value(m.get_item("forced_discharge_soc_limit"), 15), 15)

    def test_time(self):
        co_start = m.get_item("ac_charge_time_1_start")
        self.assertIsNotNone(co_start)
        self.assertEqual(co_start["kind"], "time")
        # register raw (minute<<8)|hour within the item's bit range
        self.assertEqual(m.extract_value(co_start, 0x2F0A), "10:47")


class TestEncode(unittest.TestCase):
    def test_toggle(self):
        self.assertEqual(m.encode_value(m.get_item("buzzer"), 1), 1)
        self.assertEqual(m.encode_value(m.get_item("buzzer"), 0), 0)
        self.assertEqual(m.encode_value(m.get_item("buzzer"), False), 0)

    def test_select_validates(self):
        item = m.get_item("eps_frequency")
        self.assertEqual(m.encode_value(item, 50), 50)
        with self.assertRaises(ValueError):
            m.encode_value(item, 99)

    def test_number_range_and_scale(self):
        vol = m.get_item("cutoff_voltage_eps")
        self.assertEqual(vol["scale"], 0.1)
        self.assertEqual(m.encode_value(vol, 46.5), 465)
        with self.assertRaises(ValueError):
            m.encode_value(vol, 999)

    def test_time_encode_and_decode(self):
        co_start = m.get_item("ac_charge_time_1_start")
        raw = m.encode_value(co_start, "10:47")
        self.assertEqual(raw, 0x2F0A)
        with self.assertRaises(ValueError):
            m.encode_value(co_start, "25:00")
        with self.assertRaises(ValueError):
            m.encode_value(co_start, "10:99")

    def test_merge_preserves_other_bits(self):
        item = m.get_item("ac_charge_type")
        merged = m.merge_raw(item, 0x2080, m.encode_value(item, 3))
        self.assertEqual(merged & 0xFFFF, 0x2086)
        self.assertEqual(m.extract_value(item, merged), 3)
        # untouched bits still readable
        buzzer = m.get_item("buzzer")
        self.assertEqual(m.extract_value(buzzer, merged), 1)

    def test_high_byte_round_trip(self):
        ac_power = m.get_item("ac_charge_power")
        raw = m.encode_value(ac_power, 100)
        self.assertEqual(raw, 0x6400)
        self.assertEqual(m.extract_value(ac_power, raw), 100)
        with self.assertRaises(ValueError):
            m.encode_value(ac_power, 101)

        soc = m.get_item("charge_first_soc_limit")
        raw = m.encode_value(soc, 85)
        self.assertEqual(raw, 0x5500)
        self.assertEqual(m.extract_value(soc, raw), 85)


class TestPublicItem(unittest.TestCase):
    def test_full_register_item(self):
        item = m.get_item("grid_export_percent")
        pub = m.public_item(item)
        self.assertEqual(pub["key"], "grid_export_percent")
        self.assertEqual(pub["kind"], "number")
        self.assertTrue(pub["danger"])
        self.assertFalse(pub["verify"])
        self.assertNotIn("bit", pub)

    def test_bitfield_item(self):
        item = m.get_item("ac_charge_type")
        pub = m.public_item(item)
        self.assertIn("bit0", pub)
        self.assertTrue(pub["options"])
        # FE-only contract: the payload option carries NO per-language label
        # (text lives in the FE locale mirror modbus.opt.<regKey>.<value>).
        self.assertTrue(all(o.keys() <= {"value"} for o in pub["options"]))
        self.assertTrue(all(opt["value"] == opt["value"] for opt in pub["options"]))

    def test_time_item(self):
        pub = m.public_item(m.get_item("ac_charge_time_1_start"))
        self.assertEqual(pub["kind"], "time")
        self.assertNotIn("options", pub)

    def test_language_labels(self):
        """FE locale = sole per-language text source. The register payload carries NO
        per-language name; the bilingual wording lives in the FE locale mirror only."""
        item = m.get_item("buzzer")
        pub = m.public_item(item)
        self.assertNotIn("name", pub)
        asrt = m.public_item(item)
        self.assertNotIn("name", asrt)


if __name__ == "__main__":
    unittest.main()