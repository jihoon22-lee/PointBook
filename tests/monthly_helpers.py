"""서버가 발급한 검수 버전으로 정상 월간 흐름을 수행하는 HTTP 테스트 도우미."""

from html.parser import HTMLParser


class ReviewFields(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields = {}
        self.active = False
        self.select_name = None
        self.textarea_name = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.active = attrs.get("id") == "review-form"
        if not self.active:
            return
        if tag == "input" and attrs.get("name") and attrs.get("type") != "checkbox":
            if attrs["name"] == "preserved_absent_carry":
                self.fields.setdefault(attrs["name"], []).append(attrs.get("value", ""))
            else:
                self.fields[attrs["name"]] = attrs.get("value", "")
        if tag == "select":
            self.select_name = attrs.get("name")
        if tag == "textarea":
            self.textarea_name = attrs.get("name")
            if self.textarea_name:
                self.fields[self.textarea_name] = ""
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
        if tag == "textarea" and self.textarea_name:
            # HTML textarea의 시작 태그 직후 줄바꿈 하나는 브라우저가 생략한다.
            value = self.fields[self.textarea_name]
            self.fields[self.textarea_name] = value.removeprefix("\n")
            self.textarea_name = None

    def handle_data(self, data):
        if self.active and self.textarea_name:
            self.fields[self.textarea_name] += data


def review_fields(response):
    parser = ReviewFields()
    parser.feed(response.text)
    return parser.fields


class PendingProfileChoices(HTMLParser):
    def __init__(self, side):
        super().__init__()
        self.side = side
        self.values = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if (
            tag == "button"
            and attrs.get("name") == "profile_choice"
            and attrs.get("data-choice-pending") == "yes"
            and attrs.get("value", "").endswith(":" + self.side)
            and "disabled" not in attrs
        ):
            self.values.append(attrs["value"])


def resolve_profile_choices(client, response, side="incoming"):
    """보이는 미선택 비교를 실제 HTTP 선택으로 해결한다. 유형 금지는 우회하지 않는다."""
    for _ in range(10_001):
        parser = PendingProfileChoices(side)
        parser.feed(response.text)
        if not parser.values:
            return response
        values = review_fields(response)
        values["profile_choice"] = parser.values[0]
        response = client.post("/monthly/choose", data=values)
        if response.status_code in {409, 500}:
            return response
    raise AssertionError("프로필 선택이 완료되지 않았습니다.")


def reviewed_confirm(client, data, **kwargs):
    data = dict(data)
    for key in list(data):
        if key.startswith("point_no_"):
            data.setdefault(key.replace("point_no_", "account_type_"), "person")
    response = client.post("/monthly/review", data=data)
    response = resolve_profile_choices(client, response)
    reviewed = review_fields(response)
    for key, value in data.items():
        if key.startswith("deactivated_carry_") and key in reviewed:
            reviewed[key] = value
    reviewed["ack_warnings"] = "yes"
    return client.post("/monthly/confirm", data=reviewed, **kwargs)
