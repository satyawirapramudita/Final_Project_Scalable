"""Uji /health dan /metrics — kontrak wajib semua layanan (§8 aturan koordinasi)."""

from __future__ import annotations


class TestHealth:
    def test_status_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "service": "auth"}


class TestMetrics:
    def test_menampilkan_counter_request(self, client):
        client.get("/api/auth/me")
        text = client.get("/metrics").text
        assert "http_requests_total" in text
        assert 'service="auth"' in text
        # Label path memakai template route, bukan URL mentah.
        assert 'path="/api/auth/me"' in text

    def test_counter_berkuraman_setelah_request(self, client):
        before = 0
        client.get("/api/auth/me")
        for line in client.get("/metrics").text.splitlines():
            if line.startswith("http_requests_total{") and 'path="/api/auth/me"' in line:
                before += float(line.rsplit(" ", 1)[1])
        assert before >= 1.0

    def test_content_type_prometheus(self, client):
        resp = client.get("/metrics")
        assert resp.headers["content-type"].startswith("text/plain")
