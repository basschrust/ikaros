from itertools import count

class IdGenerator:
    def __init__(self):
        self._counter = count(0)

    def next(self) -> int:
        return next(self._counter)

id_generator = IdGenerator()
