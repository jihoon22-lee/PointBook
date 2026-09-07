"""서버가 발급한 검수 버전으로 정상 월간 흐름을 수행하는 HTTP 테스트 도우미."""

from html.parser import HTMLParser


class ReviewFields(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields = {}
        self.active = False
        self.select_name = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.active = attrs.get("id") == "review-form"
        if not self.active:
            return
        if tag == "input" and attrs.get("name") and attrs.get("type") != "checkbox":
            self.fields[attrs["name"]] = attrs.get("value", "")
        if tag == "select":
            self.select_name = attrs.get("name")
        if (
            tag == "option"
            and self.select_name
            and (self.select_name not in self.fields or "selected" in attrs)
        ):
            self.fields[self.select_name] = attrs.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self.active = False
        if tag == "select":
            self.select_name = None


def review_fields(response):
    parser = ReviewFields()
    parser.feed(response.text)
    return parser.fields


def reviewed_confirm(client, data, **kwargs):
    data = dict(data)
    for key in list(data):
        if key.startswith("point_no_"):
            data.setdefault(key.replace("point_no_", "account_type_"), "person")
    response = client.post("/monthly/review", data=data)
    reviewed = review_fields(response)
    reviewed["ack_warnings"] = "yes"
    return client.post("/monthly/confirm", data=reviewed, **kwargs)
