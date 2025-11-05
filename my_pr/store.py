import redis


class Store:
    """
    Класс для работы с хранилищем данных на основе Redis.
    """

    def __init__(self, host="localhost", port=6379, db=0, retries=3, timeout=1):
        """
        Инициализирует экземпляр класса Store и устанавливает соединение с Redis.
        """
        self.host = host
        self.port = port
        self.db = db
        self.retries = retries
        self.timeout = timeout
        self._connect()

    def _connect(self):
        """
        Устанавливает соединение с Redis-сервером.
        """
        self.client = redis.Redis(
            host=self.host,
            port=self.port,
            db=self.db,
            socket_timeout=self.timeout,
            socket_connect_timeout=self.timeout,
            decode_responses=True,
        )

    def _execute_with_retry(self, func, *args, **kwargs):
        """
        Выполняет команду Redis с логикой повторных попыток.
        """
        for attempt in range(self.retries):
            try:
                return func(*args, **kwargs)
            except redis.ConnectionError:
                if attempt == self.retries - 1:
                    raise
                self._connect()
        raise redis.ConnectionError("Failed after retries")

    def get(self, key):
        """
        Получает значение по ключу из постоянного хранилища.
        """
        return self._execute_with_retry(self.client.get, key)

    def cache_get(self, key):
        """
        Получает значение из кеша по ключу.
        """
        try:
            return self._execute_with_retry(self.client.get, key)
        except redis.ConnectionError:
            return None

    def cache_set(self, key, value, ttl):
        """
        Сохраняет значение в кеш по ключу с заданным временем жизни.

        """
        try:
            self._execute_with_retry(self.client.setex, key, ttl, value)
        except redis.ConnectionError:
            pass
