class BaseService:
    def __init__(self, db, cache, llm_client, queue, logger):
        self.db = db
        self.cache = cache
        self.llm = llm_client
        self.queue = queue
        self.logger = logger


