from backend.providers.openai_compat import friendly_provider_error


def test_a_refused_connection_is_human() -> None:
    dump = (
        "HTTPConnectionPool(host='api.example.test', port=443): Max retries exceeded with url: "
        "/v1/chat/completions (Caused by NewConnectionError("
        "\"<urllib3.connection.HTTPConnection object at 0x1>: Failed to establish a new "
        "connection: [WinError 10061] No connection could be made because the target machine "
        "actively refused it\"))"
    )
    text = friendly_provider_error(dump, display_name="OpenAI", hint="Check the OpenAI key.")
    assert "HTTPConnectionPool" not in text and "WinError" not in text
    assert text.startswith("OpenAI could not be reached.")


def test_a_plain_message_stays() -> None:
    assert friendly_provider_error("Pick a model first.", display_name="Groq") == "Pick a model first."


def test_cloud_host_refusal_names_the_host() -> None:
    text = friendly_provider_error("Max retries exceeded ... actively refused it", display_name="Groq", hint="Check the Groq key.")
    assert text.startswith("Groq could not be reached.")
    assert "Check the Groq key." in text
