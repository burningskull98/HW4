import json
import datetime
import logging
import hashlib
import uuid
from argparse import ArgumentParser
from http.server import BaseHTTPRequestHandler, HTTPServer
from my_pr.scoring import get_score, get_interests
from my_pr.store import Store

SALT = "Otus"
ADMIN_LOGIN = "admin"
ADMIN_SALT = "42"
OK = 200
BAD_REQUEST = 400
FORBIDDEN = 403
NOT_FOUND = 404
INVALID_REQUEST = 422
INTERNAL_ERROR = 500
ERRORS = {
    BAD_REQUEST: "Bad Request",
    FORBIDDEN: "Forbidden",
    NOT_FOUND: "Not Found",
    INVALID_REQUEST: "Invalid Request",
    INTERNAL_ERROR: "Internal Server Error",
}
UNKNOWN = 0
MALE = 1
FEMALE = 2
GENDERS = {
    UNKNOWN: "unknown",
    MALE: "male",
    FEMALE: "female",
}


class Field:
    """
    Базовый класс для валидации полей.
    """

    def __init__(self, required=False, nullable=True):
        self.required = required
        self.nullable = nullable

    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, instance, owner):
        if instance is None:
            return self
        return instance.__dict__.get(self.name)

    def __set__(self, instance, value):
        if value is None and self.required:
            raise ValueError(f"{self.name} is required")
        if value is None and not self.nullable:
            raise ValueError(f"{self.name} cannot be null")
        if value is not None:
            value = self.validate(value)
        instance.__dict__[self.name] = value

    def validate(self, value):
        return value


class CharField(Field):
    """
    Поле для строковых значений.
    """

    def validate(self, value):
        if not isinstance(value, str):
            raise ValueError(f"{self.name} must be a string")
        if not self.nullable and value == "":
            raise ValueError(f"{self.name} cannot be empty")
        return value


class ArgumentsField(Field):
    """
    Поле для хранения словаря аргументов.
    """

    def validate(self, value):
        if not isinstance(value, dict):
            raise ValueError("arguments must be a dict")
        return value


class EmailField(CharField):
    """
    Поле для электронной почты.
    """

    def validate(self, value):
        super().validate(value)
        if value and "@" not in value:
            raise ValueError("invalid email")
        return value


class PhoneField(Field):
    """
    Поле для телефонного номера.
    """

    def validate(self, value):
        if isinstance(value, str):
            if not value.isdigit():
                raise ValueError("phone must be digits")
            value = int(value)
        if not isinstance(value, int):
            raise ValueError("phone must be int or string of digits")
        if len(str(value)) != 11 or str(value)[0] != "7":
            raise ValueError("phone must be 11 digits starting with 7")
        return value


class DateField(Field):
    """
    Поле для даты.
    """

    def validate(self, value):
        if not isinstance(value, str):
            raise ValueError("date must be string")
        try:
            datetime.datetime.strptime(value, "%d.%m.%Y")
        except ValueError:
            raise ValueError("invalid date format")
        return value


class BirthDayField(DateField):
    """
    Поле для даты рождения.
    """

    def validate(self, value):
        super().validate(value)
        date = datetime.datetime.strptime(value, "%d.%m.%Y")
        if date < datetime.datetime.now() - datetime.timedelta(days=70 * 365):
            raise ValueError("birthday too old")
        return value


class GenderField(Field):
    """
    Поле для пола.
    """

    def validate(self, value):
        if not isinstance(value, int) or value not in [0, 1, 2]:
            raise ValueError("gender must be 0, 1 or 2")
        return value


class ClientIDsField(Field):
    """
    Поле для списка ID клиентов.
    """

    def validate(self, value):
        if not isinstance(value, list):
            raise ValueError("client_ids must be list")
        if not value:
            raise ValueError("client_ids cannot be empty")
        for id in value:
            if not isinstance(id, int):
                raise ValueError("client_ids must be list of ints")
        return value


class ClientsInterestsRequest(object):
    """
    Запрос для получения интересов клиентов.
    """

    client_ids = ClientIDsField(required=True)
    date = DateField(required=False, nullable=True)

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class OnlineScoreRequest(object):
    """
    Запрос для получения скоринга.
    """

    first_name = CharField(required=False, nullable=True)
    last_name = CharField(required=False, nullable=True)
    email = EmailField(required=False, nullable=True)
    phone = PhoneField(required=False, nullable=True)
    birthday = BirthDayField(required=False, nullable=True)
    gender = GenderField(required=False, nullable=True)

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class MethodRequest(object):
    """
    Базовый класс для запросов к методам API.
    """

    account = CharField(required=False, nullable=True)
    login = CharField(required=True, nullable=True)
    token = CharField(required=True, nullable=True)
    arguments = ArgumentsField(required=True, nullable=True)
    method = CharField(required=True, nullable=True)

    def __init__(self, **kwargs):
        allowed_fields = {"account", "login", "token", "arguments", "method"}
        for key, value in kwargs.items():
            if key not in allowed_fields:
                raise ValueError(f"Unknown field: {key}")
            setattr(self, key, value)

    @property
    def is_admin(self):
        return self.login == ADMIN_LOGIN


def check_auth(request):
    """
    Проверяет аутентификацию запроса.
    """
    if request.is_admin:
        digest = hashlib.sha512(
            (datetime.datetime.now().strftime("%Y%m%d%H") + ADMIN_SALT).encode("utf-8")
        ).hexdigest()
    else:
        digest = hashlib.sha512(
            (str(request.account or "") + str(request.login or "") + SALT).encode(
                "utf-8"
            )
        ).hexdigest()
    return digest == request.token


def method_handler(request, ctx, store):
    """
    Обработчик HTTP-запросов к методам API.
    """
    body = request["body"]
    try:
        req = MethodRequest(**body)
    except ValueError as e:
        return str(e), INVALID_REQUEST

    if not check_auth(req):
        return "Forbidden", FORBIDDEN

    method = req.method
    arguments = req.arguments

    if method == "online_score":
        try:
            args_obj = OnlineScoreRequest(**arguments)
        except ValueError as e:
            return str(e), INVALID_REQUEST

        pairs = [
            (args_obj.phone, args_obj.email),
            (args_obj.first_name, args_obj.last_name),
            (args_obj.gender, args_obj.birthday),
        ]
        if not any(all(p is not None and p != "" for p in pair) for pair in pairs):
            return "at least one pair must be filled", INVALID_REQUEST

        has = [k for k, v in arguments.items() if v is not None and v != ""]
        ctx["has"] = has

        if req.is_admin:
            score = 42
        else:
            score = get_score(
                store,
                args_obj.phone,
                args_obj.email,
                args_obj.birthday,
                args_obj.gender,
                args_obj.first_name,
                args_obj.last_name,
            )
        return {"score": score}, OK

    elif method == "clients_interests":
        try:
            args_obj = ClientsInterestsRequest(**arguments)
        except ValueError as e:
            return str(e), INVALID_REQUEST

        ctx["nclients"] = len(args_obj.client_ids)

        response = {}
        for cid in args_obj.client_ids:
            response[str(cid)] = get_interests(store, cid)
        return response, OK

    else:
        return "unknown method", NOT_FOUND


class MainHTTPHandler(BaseHTTPRequestHandler):
    """
    Обработчик HTTP-запросов.
    """

    router = {"method": method_handler}
    store = None

    def get_request_id(self, headers):
        """Получает ID запроса из заголовков или генерирует новый."""
        return headers.get("HTTP_X_REQUEST_ID", uuid.uuid4().hex)

    def do_POST(self):
        """
        Обрабатывает входящие запросы, выполняет валидацию и маршрутизацию.
        """
        response, code = {}, OK
        context = {"request_id": self.get_request_id(self.headers)}
        request = None
        try:
            data_string = self.rfile.read(int(self.headers["Content-Length"]))
            request = json.loads(data_string)
        except:
            code = BAD_REQUEST

        if request:
            path = self.path.strip("/")
            logging.info("%s: %s %s" % (self.path, data_string, context["request_id"]))
            if path in self.router:
                try:
                    response, code = self.router[path](
                        {"body": request, "headers": self.headers}, context, self.store
                    )
                except Exception as e:
                    logging.exception("Unexpected error: %s" % e)
                    code = INTERNAL_ERROR
            else:
                code = NOT_FOUND

        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if code not in ERRORS:
            r = {"response": response, "code": code}
        else:
            r = {"error": response or ERRORS.get(code, "Unknown Error"), "code": code}
        context.update(r)
        logging.info(context)
        self.wfile.write(json.dumps(r).encode("utf-8"))
        return


if __name__ == "__main__":
    store = Store()
    MainHTTPHandler.store = store
    parser = ArgumentParser()
    parser.add_argument("-p", "--port", action="store", type=int, default=8080)
    parser.add_argument("-l", "--log", action="store", default=None)
    args = parser.parse_args()
    logging.basicConfig(
        filename=args.log,
        level=logging.INFO,
        format="[%(asctime)s] %(levelname).1s %(message)s",
        datefmt="%Y.%m.%d %H:%M:%S",
    )
    server = HTTPServer(("localhost", args.port), MainHTTPHandler)
    logging.info("Starting server at %s" % args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()
