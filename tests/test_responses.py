from freecad_mcp.responses import json_response


def test_json_response_is_compact():
    text = json_response({"a": [1, 2], "b": {"c": True}})[0].text
    assert text == '{"a":[1,2],"b":{"c":true}}'


def test_json_response_handles_non_serializable():
    class Odd:
        def __str__(self):
            return "odd"

    assert json_response({"x": Odd()})[0].text == '{"x":"odd"}'
