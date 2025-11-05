from unittest.mock import Mock, patch
import json
import pytest
from my_pr.api import (
    CharField,
    ArgumentsField,
    EmailField,
    PhoneField,
    DateField,
    BirthDayField,
    GenderField,
    ClientIDsField,
    MethodRequest,
    check_auth,
    method_handler,
    OK,
    FORBIDDEN,
    INVALID_REQUEST,
)
from my_pr.store import Store
import redis


def cases(test_cases):
    def decorator(func):
        @pytest.mark.parametrize("case", test_cases, ids=lambda x: f"case: {x}")
        def wrapper(self, case):
            func(self, case)

        return wrapper

    return decorator


class StoreMock:
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def cache_get(self, key):
        return self.data.get(key)

    def cache_set(self, key, value, ttl):
        self.data[key] = value


def call_api(body, store):
    request = {"body": body, "headers": {}}
    ctx = {}
    return method_handler(request, ctx, store)


@pytest.fixture
def mock_redis(mocker):
    mock_client = mocker.MagicMock()
    mocker.patch("redis.Redis", return_value=mock_client)
    return mock_client


@pytest.fixture
def store(mock_redis):
    return Store(host="localhost", port=6379, db=0, retries=3, timeout=1)


def test_init(mocker):
    mock_redis = mocker.patch("redis.Redis")
    store = Store(host="test_host", port=1234, db=1, retries=5, timeout=2)
    mock_redis.assert_called_once_with(
        host="test_host",
        port=1234,
        db=1,
        socket_timeout=2,
        socket_connect_timeout=2,
        decode_responses=True,
    )
    assert store.host == "test_host"
    assert store.port == 1234
    assert store.db == 1
    assert store.retries == 5
    assert store.timeout == 2


def test_get_success(store, mock_redis):
    mock_redis.get.return_value = "value"
    result = store.get("key")
    mock_redis.get.assert_called_with("key")
    assert result == "value"


def test_get_with_retry(store, mock_redis):
    mock_redis.get.side_effect = [redis.ConnectionError, redis.ConnectionError, "value"]
    result = store.get("key")
    assert mock_redis.get.call_count == 3
    assert result == "value"


# Юнит-тесты
class TestFields:
    def test_char_field(self):
        field = CharField(required=True, nullable=False)

        class TestObj:
            name = field

        obj = TestObj()
        obj.name = "test"
        assert obj.name == "test"

        with pytest.raises(ValueError, match="name is required"):
            obj.name = None

        with pytest.raises(ValueError, match="name cannot be empty"):
            obj.name = ""

        field_nullable = CharField(required=False, nullable=True)

        class TestObjNullable:
            name = field_nullable

        obj_nullable = TestObjNullable()
        obj_nullable.name = None
        assert obj_nullable.name is None

    def test_email_field(self):
        field = EmailField(required=True, nullable=False)

        class TestObj:
            email = field

        obj = TestObj()
        obj.email = "test_ex@gmail.com"

        with pytest.raises(ValueError, match="invalid email"):
            obj.email = "invalid"

    def test_phone_field(self):
        field = PhoneField(required=True, nullable=False)

        class TestObj:
            phone = field

        obj = TestObj()
        obj.phone = "79865432101"
        assert obj.phone == 79865432101

        with pytest.raises(ValueError, match="phone must be 11 digits starting with 7"):
            obj.phone = "89865432101"

    def test_date_field(self):
        field = DateField(required=True, nullable=False)

        class TestObj:
            date = field

        obj = TestObj()
        obj.date = "30.01.1995"

        with pytest.raises(ValueError, match="invalid date format"):
            obj.date = "1995-01-30"

    def test_birthday_field(self):
        field = BirthDayField(required=True, nullable=False)

        class TestObj:
            birthday = field

        obj = TestObj()
        obj.birthday = "30.01.1995"

        with pytest.raises(ValueError, match="birthday too old"):
            obj.birthday = "01.01.1940"

    def test_gender_field(self):
        field = GenderField(required=True, nullable=False)

        class TestObj:
            gender = field

        obj = TestObj()
        obj.gender = 1

        with pytest.raises(ValueError, match="gender must be 0, 1 or 2"):
            obj.gender = 3

    def test_client_ids_field(self):
        field = ClientIDsField(required=True, nullable=False)

        class TestObj:
            client_ids = field

        obj = TestObj()
        obj.client_ids = [1, 2, 3]

        with pytest.raises(ValueError, match="client_ids cannot be empty"):
            obj.client_ids = []

    def test_arguments_field(self):
        field = ArgumentsField(required=True, nullable=False)

        class TestObj:
            arguments = field

        obj = TestObj()
        obj.arguments = {"key": "value"}

        with pytest.raises(ValueError, match="arguments must be a dict"):
            obj.arguments = "not_dict"


class TestAuth:
    @patch("my_pr.api.datetime")
    @patch("my_pr.api.hashlib.sha512")
    def test_check_auth_admin(self, mock_sha512, mock_datetime):
        mock_now = Mock()
        mock_now.strftime.return_value = "2023100112"
        mock_datetime.datetime.now.return_value = mock_now

        mock_digest = Mock()
        mock_digest.hexdigest.return_value = "expected_digest"
        mock_sha512.return_value = mock_digest

        req = MethodRequest(login="admin", token="expected_digest")
        assert check_auth(req)

    @patch("my_pr.api.hashlib.sha512")
    def test_check_auth_user(self, mock_sha512):
        mock_digest = Mock()
        mock_digest.hexdigest.return_value = "expected_digest"
        mock_sha512.return_value = mock_digest

        req = MethodRequest(account="account", login="user", token="expected_digest")
        assert check_auth(req)


class TestMethodHandler:
    def test_invalid_request(self):
        store = Mock()
        body = {"invalid": "data"}
        response, code = method_handler({"body": body, "headers": {}}, {}, store)
        assert code == INVALID_REQUEST

    def test_bad_auth(self):
        store = Mock()
        body = {
            "account": "horns&hoofs",
            "login": "h&f",
            "method": "online_score",
            "token": "wrong_token",
            "arguments": {},
        }
        response, code = method_handler({"body": body, "headers": {}}, {}, store)
        assert code == FORBIDDEN

    def test_online_score_invalid_args(self):
        store = Mock()
        body = {
            "account": "horns&hoofs",
            "login": "h&f",
            "method": "online_score",
            "token": "digest",
            "arguments": {"phone": "invalid"},
        }

        from my_pr import api

        original_check_auth = api.check_auth
        api.check_auth = Mock(return_value=True)

        response, code = method_handler({"body": body, "headers": {}}, {}, store)
        assert code == INVALID_REQUEST

        api.check_auth = original_check_auth

    def test_clients_interests_invalid_args(self):
        store = Mock()
        body = {
            "account": "horns&hoofs",
            "login": "h&f",
            "method": "clients_interests",
            "token": "digest",
            "arguments": {"client_ids": []},
        }
        import my_pr.api

        original_check_auth = my_pr.api.check_auth
        my_pr.api.check_auth = Mock(return_value=True)

        response, code = method_handler({"body": body, "headers": {}}, {}, store)
        assert code == INVALID_REQUEST

        my_pr.api.check_auth = original_check_auth


# Интеграционные тесты
class TestIntegration:
    def setup_method(self):
        self.store = StoreMock()

    def test_online_score_with_store(self):
        self.store.cache_set("uid:some_key", "5.0", 3600)

        body = {
            "account": "horns&hoofs",
            "login": "h&f",
            "method": "online_score",
            "token": "digest",
            "arguments": {"phone": "79865432101", "email": "test_ex@gmail.com"},
        }
        from my_pr import api

        original_check_auth = api.check_auth
        api.check_auth = Mock(return_value=True)

        response, code = method_handler({"body": body, "headers": {}}, {}, self.store)
        assert code == OK
        assert "score" in response

        api.check_auth = original_check_auth

    def test_clients_interests_with_store(self):
        self.store.data["i:1"] = json.dumps(["music", "books"])

        body = {
            "account": "horns&hoofs",
            "login": "h&f",
            "method": "clients_interests",
            "token": "digest",
            "arguments": {"client_ids": [1]},
        }
        from my_pr import api

        original_check_auth = api.check_auth
        api.check_auth = Mock(return_value=True)

        response, code = method_handler({"body": body, "headers": {}}, {}, self.store)
        assert code == OK
        assert response["1"] == ["music", "books"]

        api.check_auth = original_check_auth


# Функциональные тесты
class TestFunctional:
    def setup_method(self):
        self.store = StoreMock()

    @cases(
        [
            {
                "account": "horns&hoofs",
                "login": "h&f",
                "method": "online_score",
                "token": "wrong",
                "arguments": {},
            },
            {
                "account": "horns&hoofs",
                "login": "h&f",
                "method": "online_score",
                "token": "sdd",
                "arguments": {},
            },
            {
                "account": "horns&hoofs",
                "login": "admin",
                "method": "online_score",
                "token": "",
                "arguments": {},
            },
        ]
    )
    def test_bad_auth(self, case):
        from my_pr import api

        original_check_auth = api.check_auth
        api.check_auth = Mock(return_value=False)

        response, code = call_api(case, self.store)
        assert code == FORBIDDEN

        api.check_auth = original_check_auth

    def test_online_score_success(self):
        body = {
            "account": "horns&hoofs",
            "login": "h&f",
            "method": "online_score",
            "token": "digest",
            "arguments": {"phone": "79865432101", "email": "test_ex@gmail.com"},
        }
        from my_pr import api

        original_check_auth = api.check_auth
        api.check_auth = Mock(return_value=True)

        response, code = call_api(body, self.store)
        assert code == OK
        assert isinstance(response["score"], (int, float))

        api.check_auth = original_check_auth

    def test_clients_interests_success(self):
        self.store.data["i:1"] = json.dumps(["sport"])

        body = {
            "account": "horns&hoofs",
            "login": "h&f",
            "method": "clients_interests",
            "token": "digest",
            "arguments": {"client_ids": [1]},
        }
        from my_pr import api

        original_check_auth = api.check_auth
        api.check_auth = Mock(return_value=True)

        response, code = call_api(body, self.store)
        assert code == OK
        assert response["1"] == ["sport"]

        api.check_auth = original_check_auth
