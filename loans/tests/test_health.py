from django.test import TestCase


class HealthzTests(TestCase):
    def test_healthz_ok_without_auth(self):
        response = self.client.get("/healthz/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "database": "ok"})

    def test_healthz_rejects_post(self):
        response = self.client.post("/healthz/")
        self.assertEqual(response.status_code, 405)
