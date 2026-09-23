from mae.domain import Work
from mae.seerr import Seerr


class MutableSeerr(Seerr):
    def __init__(self, requests):
        self._requests = requests
        self.known = {}

    def requests(self):
        # Rows omitted from the list still exist in Seerr with their last status.
        self.known.update({row["id"]: row for row in self._requests})
        return self._requests

    def request_status(self, request_id):
        return self.known.get(request_id, {"id": request_id, "status": 2})

    def work(self, request):
        return Work(kind="movie", external_id=17, title="Example", year=2024)


def request(request_id, status):
    return {"id": request_id, "status": status, "type": "movie", "media": {"tmdbId": 17}}


def test_explicit_seerr_cancellation_and_new_request_generation(settings, db):
    seerr = MutableSeerr([request(100, 2)])
    assert seerr.reconcile(db, True) == 1
    job_id = db.jobs()[0]["id"]
    assert db.job(job_id)["state"] == "REQUESTED"
    seerr._requests = [request(100, 3)]
    assert seerr.reconcile(db, True) == 0
    assert db.job(job_id)["state"] == "CANCELLED"
    seerr._requests = [request(101, 2)]
    seerr.reconcile(db, True)
    assert db.job(job_id)["generation"] == 2
    seerr.reconcile(db, True)
    assert db.job(job_id)["generation"] == 2


def test_missing_seerr_row_is_not_a_cancellation(settings, db):
    seerr = MutableSeerr([request(100, 2)])
    seerr.reconcile(db, True)
    job_id = db.jobs()[0]["id"]
    seerr._requests = []
    seerr.reconcile(db, True)
    assert db.job(job_id)["state"] == "REQUESTED"


def test_one_terminal_request_does_not_cancel_another_active_request(settings, db):
    seerr = MutableSeerr([request(100, 2), request(101, 2)])
    seerr.reconcile(db, True)
    job_id = db.jobs()[0]["id"]
    seerr._requests = [request(100, 3), request(101, 2)]
    seerr.reconcile(db, True)
    assert db.job(job_id)["state"] == "REQUESTED"


def test_terminal_request_never_removes_already_available_library_item(settings, db):
    seerr = MutableSeerr([request(100, 2)])
    seerr.reconcile(db, True)
    job_id = db.jobs()[0]["id"]
    db.transition(job_id, "AVAILABLE")
    seerr._requests = [request(100, 5)]
    seerr.reconcile(db, True)
    assert db.job(job_id)["state"] == "AVAILABLE"


def test_admin_cancel_is_not_reopened_by_the_same_seerr_request(settings, db):
    seerr = MutableSeerr([request(100, 2)])
    seerr.reconcile(db, True)
    job_id = db.jobs()[0]["id"]
    assert db.cancel_from_admin(job_id)
    seerr.reconcile(db, True)
    assert db.job(job_id)["state"] == "CANCELLED"
    seerr._requests = [request(101, 2)]
    seerr.reconcile(db, True)
    assert db.job(job_id)["state"] == "REQUESTED"
    assert db.job(job_id)["generation"] == 2
