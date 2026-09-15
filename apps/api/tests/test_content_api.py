"""Content zones through FastAPI: who may manage them, and who actually sees the files."""

import os
import unittest

try:
    from fastapi.testclient import TestClient

    from fleetcontrol_api.main import store
    from tests.helpers import signed_in
except ImportError:  # pragma: no cover
    TestClient = None


@unittest.skipIf(TestClient is None, "fastapi/httpx not installed")
class ContentApiTest(unittest.TestCase):
    def setUp(self):
        self.admin = signed_in("admin")
        self.zone = "cases-" + os.urandom(3).hex()
        r = self.admin.post("/api/v1/content/zones", json={"id": self.zone, "name": "Case files",
                                                           "description": "Investigation material", "read_roles": ["approver"]})
        self.assertEqual(r.status_code, 201, r.text)
        self.addCleanup(self._clean)

    def _clean(self):
        for f in store.list_files([self.zone]):
            store.delete_file(f["id"])
        store.delete_zone(self.zone)

    def _mine(self, client):
        return {z["id"]: z for z in client.get("/api/v1/content/zones").json()}

    def test_zones_are_made_edited_and_protected(self):
        zone = self._mine(self.admin)[self.zone]
        self.assertEqual((zone["files"], zone["read_roles"], zone["may_read"]), (0, ["approver"], True))
        self.assertEqual(self.admin.post("/api/v1/content/zones", json={"id": self.zone, "name": "Again"}).status_code, 409)
        self.assertEqual(self.admin.post("/api/v1/content/zones", json={"id": "9lives", "name": "Bad"}).status_code, 422)
        r = self.admin.patch(f"/api/v1/content/zones/{self.zone}", json={"read_roles": ["approver", "viewer", "approver"]})
        self.assertEqual(r.json()["read_roles"], ["approver", "viewer"])
        self.assertEqual(self.admin.patch("/api/v1/content/zones/nope", json={"name": "x"}).status_code, 404)

        managed = "managed-" + os.urandom(3).hex()
        store.save_zone({"id": managed, "name": "Sanctions lists", "description": "", "read_roles": ["viewer"], "managed": True,
                         "source": "opensanctions", "created_by": "fleetcontrol", "created_at": 1.0})
        self.addCleanup(store.delete_zone, managed)
        self.assertEqual(self.admin.patch(f"/api/v1/content/zones/{managed}", json={"name": "x"}).status_code, 409)
        self.assertEqual(self.admin.post("/api/v1/content/files", json={"zone": managed, "name": "x.csv"}).status_code, 409)

    def test_files_are_only_seen_by_the_roles_of_their_zone(self):
        r = self.admin.post("/api/v1/content/files", json={"zone": self.zone, "name": "wire log.csv",
                                                           "classification": "restricted", "text": "a,b\n1,2\n"})
        self.assertEqual(r.status_code, 201, r.text)
        file_id = r.json()["id"]
        self.assertNotIn("text", r.json())
        self.assertEqual(self.admin.get(f"/api/v1/content/files/{file_id}").json()["text"], "a,b\n1,2\n")
        self.assertEqual([f["name"] for f in self.admin.get("/api/v1/content/files", params={"zone": self.zone}).json()], ["wire log.csv"])

        approver = signed_in("approver")
        self.assertEqual([f["id"] for f in approver.get("/api/v1/content/files", params={"zone": self.zone}).json()], [file_id])
        self.assertEqual(approver.get(f"/api/v1/content/files/{file_id}").json()["classification"], "restricted")

        operator = signed_in("operator")  # not a role on this zone
        self.assertEqual(operator.get("/api/v1/content/files", params={"zone": self.zone}).json(), [])
        self.assertEqual(operator.get(f"/api/v1/content/files/{file_id}").status_code, 404)
        self.assertFalse(self._mine(operator)[self.zone]["may_read"])

        self.assertEqual(self.admin.post("/api/v1/content/files", json={"zone": self.zone, "name": "x", "classification": "secret"}).status_code, 422)
        self.assertEqual(self.admin.post("/api/v1/content/files", json={"zone": "nope", "name": "x"}).status_code, 404)

    def test_who_may_manage_content(self):
        for role, code in (("fleet_architect", 201), ("operator", 403), ("approver", 403), ("viewer", 403)):
            r = signed_in(role).post("/api/v1/content/zones", json={"id": f"{role[:6]}-{os.urandom(2).hex()}", "name": "Zone"})
            self.assertEqual(r.status_code, code, role)
            if code == 201:
                self.addCleanup(store.delete_zone, r.json()["id"])
        self.assertEqual(signed_in("viewer").get("/api/v1/content/zones").status_code, 200)  # everyone sees the list
        self.assertEqual(signed_in("viewer").get("/api/v1/content/classifications").json(), ["internal", "confidential", "restricted"])

    def test_a_zone_with_files_is_not_deleted_by_accident(self):
        r = self.admin.post("/api/v1/content/files", json={"zone": self.zone, "name": "note.md", "text": "hello"})
        self.assertEqual(self.admin.delete(f"/api/v1/content/zones/{self.zone}").status_code, 409)
        self.assertEqual(self.admin.delete(f"/api/v1/content/files/{r.json()['id']}").status_code, 200)
        self.assertEqual(self.admin.delete(f"/api/v1/content/zones/{self.zone}").status_code, 200)
        audit = self.admin.get("/api/v1/audit").json()
        self.assertTrue(any(e["action"] == "content.zone_deleted" and e["target"] == self.zone for e in audit))


if __name__ == "__main__":
    unittest.main()
