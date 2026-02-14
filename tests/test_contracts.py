import unittest

from wg_drift.contracts import extract_contract, parse_contract, replace_contract_block, TaskContract


class ContractTests(unittest.TestCase):
    def test_extract_and_parse(self) -> None:
        desc = "hi\n```wg-contract\nschema = 1\nmode = \"core\"\nobjective = \"x\"\ntouch = [\"src/**\"]\n```\nbye\n"
        body = extract_contract(desc)
        self.assertIsNotNone(body)
        raw = parse_contract(body or "")
        c = TaskContract.from_raw(raw, fallback_objective="fallback")
        self.assertEqual(c.mode, "core")
        self.assertEqual(c.touch, ["src/**"])

    def test_replace_contract_block(self) -> None:
        desc = "hello\n\n```wg-contract\nschema = 1\nmode = \"core\"\nobjective = \"x\"\ntouch = []\n```\n\ntail\n"
        new_desc = replace_contract_block(desc, {"schema": 1, "mode": "core", "objective": "x", "touch": ["src/**"]})
        body = extract_contract(new_desc)
        self.assertIsNotNone(body)
        raw = parse_contract(body or "")
        self.assertEqual(raw.get("touch"), ["src/**"])


if __name__ == "__main__":
    unittest.main()
