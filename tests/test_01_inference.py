"""External Route: token generation and chat completions for both models."""

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class TestTokenGeneration:

    def test_token_endpoint_returns_200(self, maas_url, oc_token):
        resp = requests.post(
            f"{maas_url}/maas-api/v1/tokens",
            headers={
                "Authorization": f"Bearer {oc_token}",
                "Content-Type": "application/json",
            },
            json={"expiration": "10m"},
            verify=False,
            timeout=15,
        )
        assert resp.status_code in (200, 201)

    def test_token_response_contains_token(self, maas_url, oc_token):
        resp = requests.post(
            f"{maas_url}/maas-api/v1/tokens",
            headers={
                "Authorization": f"Bearer {oc_token}",
                "Content-Type": "application/json",
            },
            json={"expiration": "10m"},
            verify=False,
            timeout=15,
        )
        body = resp.json()
        assert "token" in body
        assert len(body["token"]) > 50, "Token looks too short"


class TestChatCompletions:

    def test_inference_returns_200(
        self, maas_url, maas_token, inference_path, chat_payload
    ):
        resp = requests.post(
            f"{maas_url}{inference_path}",
            headers={
                "Authorization": f"Bearer {maas_token}",
                "Content-Type": "application/json",
            },
            json=chat_payload,
            verify=False,
            timeout=60,
        )
        assert resp.status_code == 200

    def test_response_has_choices(
        self, maas_url, maas_token, inference_path, chat_payload
    ):
        resp = requests.post(
            f"{maas_url}{inference_path}",
            headers={
                "Authorization": f"Bearer {maas_token}",
                "Content-Type": "application/json",
            },
            json=chat_payload,
            verify=False,
            timeout=60,
        )
        body = resp.json()
        assert "choices" in body
        assert len(body["choices"]) > 0

    def test_response_content_is_nonempty(
        self, maas_url, maas_token, inference_path, chat_payload
    ):
        resp = requests.post(
            f"{maas_url}{inference_path}",
            headers={
                "Authorization": f"Bearer {maas_token}",
                "Content-Type": "application/json",
            },
            json=chat_payload,
            verify=False,
            timeout=60,
        )
        body = resp.json()
        content = body["choices"][0]["message"]["content"]
        assert content and len(content.strip()) > 0

    def test_response_model_matches(
        self, maas_url, maas_token, inference_path, chat_payload, model_name
    ):
        resp = requests.post(
            f"{maas_url}{inference_path}",
            headers={
                "Authorization": f"Bearer {maas_token}",
                "Content-Type": "application/json",
            },
            json=chat_payload,
            verify=False,
            timeout=60,
        )
        body = resp.json()
        assert body["model"] == model_name

    def test_response_has_usage(
        self, maas_url, maas_token, inference_path, chat_payload
    ):
        resp = requests.post(
            f"{maas_url}{inference_path}",
            headers={
                "Authorization": f"Bearer {maas_token}",
                "Content-Type": "application/json",
            },
            json=chat_payload,
            verify=False,
            timeout=60,
        )
        body = resp.json()
        usage = body["usage"]
        assert usage["prompt_tokens"] > 0
        assert usage["completion_tokens"] > 0


class TestModel2ChatCompletions:
    """Verify inference works on the second model (tinyllama-fast)."""

    def test_model2_inference_returns_200(
        self, maas_url, maas_token, inference_path_model2, chat_payload_model2
    ):
        resp = requests.post(
            f"{maas_url}{inference_path_model2}",
            headers={
                "Authorization": f"Bearer {maas_token}",
                "Content-Type": "application/json",
            },
            json=chat_payload_model2,
            verify=False,
            timeout=60,
        )
        assert resp.status_code == 200

    def test_model2_response_has_choices(
        self, maas_url, maas_token, inference_path_model2, chat_payload_model2
    ):
        resp = requests.post(
            f"{maas_url}{inference_path_model2}",
            headers={
                "Authorization": f"Bearer {maas_token}",
                "Content-Type": "application/json",
            },
            json=chat_payload_model2,
            verify=False,
            timeout=60,
        )
        body = resp.json()
        assert "choices" in body
        assert len(body["choices"]) > 0
        assert body["model"] == "tinyllama-fast"
