import unittest

from lumisync import groups


DEVICES = [
    {"mac": "aa:bb", "model": "H619C", "ip": "192.168.0.10"},
    {"mac": "cc:dd", "model": "H6199", "ip": "192.168.0.20"},
    {"ip": "192.168.0.30", "model": "Manual"},  # no MAC -> keyed by ip
]


class GroupModelTests(unittest.TestCase):
    def test_make_group_uses_stable_keys_and_dedups(self):
        group = groups.make_group("Desk", [DEVICES[0], DEVICES[0], DEVICES[2]])
        self.assertEqual(group["name"], "Desk")
        self.assertEqual(group["devices"], ["aa:bb", "192.168.0.30"])

    def test_resolve_devices_returns_members_in_order(self):
        group = groups.make_group("Room", [DEVICES[1], DEVICES[0]])
        resolved = groups.resolve_devices(group, DEVICES)
        self.assertEqual([d["mac"] for d in resolved], ["cc:dd", "aa:bb"])

    def test_resolve_skips_missing_devices(self):
        group = {"name": "x", "devices": ["aa:bb", "zz:zz"]}
        resolved = groups.resolve_devices(group, DEVICES)
        self.assertEqual(len(resolved), 1)

    def test_upsert_replaces_same_name_case_insensitively(self):
        g1 = groups.make_group("Living", [DEVICES[0]])
        g2 = groups.make_group("living", [DEVICES[1]])
        result = groups.upsert_group([g1], g2)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["devices"], ["cc:dd"])

    def test_remove_group(self):
        g1 = groups.make_group("A", [DEVICES[0]])
        g2 = groups.make_group("B", [DEVICES[1]])
        result = groups.remove_group([g1, g2], "a")
        self.assertEqual([g["name"] for g in result], ["B"])

    def test_list_groups_normalizes_and_drops_bad_entries(self):
        settings = {
            "groups": [
                {"name": "Good", "devices": ["aa:bb"]},
                {"devices": ["cc:dd"]},   # no name -> dropped
                "not-a-dict",             # -> dropped
            ]
        }
        listed = groups.list_groups(settings)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["name"], "Good")

    def test_list_groups_empty_when_absent(self):
        self.assertEqual(groups.list_groups({}), [])


if __name__ == "__main__":
    unittest.main()
