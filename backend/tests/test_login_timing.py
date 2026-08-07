"""Login must not reveal whether an account exists.

myMeal skipped bcrypt entirely when the user was missing, so an absent-user
login returned in ~0ms while a wrong password on a real account took a full
bcrypt (~250ms) — a 100000x timing oracle that enumerates valid emails. Both
sibling apps already run a dummy verify to equalise this; myMeal was the gap.
"""
import time



def _register(client, email="real@t.com", pw="correct-horse-battery"):
    client.post("/api/v1/users/register",
                json={"email": email, "password": pw, "name": "R"})


def test_absent_and_present_user_take_similar_time(app):
    _register(app.test_client())
    client = app.test_client()

    def timed(email):
        # median of 5 to damp scheduler noise
        samples = []
        for _ in range(5):
            t = time.perf_counter()
            client.post("/api/v1/users/login",
                        json={"username": email, "password": "wrongwrongwrong"})
            samples.append(time.perf_counter() - t)
        samples.sort()
        return samples[2]

    present = timed("real@t.com")
    absent = timed("nobody@t.com")

    # Before the fix absent was ~0ms and present ~250ms. Require them within 3x:
    # both should now pay one bcrypt.
    ratio = max(present, absent) / max(min(present, absent), 1e-6)
    assert ratio < 3, (
        f"login timing leaks account existence: present={present*1000:.0f}ms "
        f"absent={absent*1000:.0f}ms (ratio {ratio:.0f}x)")


def test_absent_user_still_gets_401(app):
    r = app.test_client().post("/api/v1/users/login",
                               json={"username": "ghost@t.com", "password": "x"})
    assert r.status_code == 401


def test_wrong_password_gets_401_and_right_password_succeeds(app):
    _register(app.test_client())
    c = app.test_client()
    assert c.post("/api/v1/users/login",
                  json={"username": "real@t.com", "password": "nope"}).status_code == 401
    ok = c.post("/api/v1/users/login",
                json={"username": "real@t.com", "password": "correct-horse-battery"})
    assert ok.status_code == 200 and ok.get_json()["token"].startswith("Bearer ")


def test_an_absurdly_long_password_still_gets_a_clean_401(app):
    """An oversized password must be rejected as invalid, never 500.

    Honesty note: the _MAX_PASSWORD_LEN bound is defence-in-depth and is NOT
    mutation-checkable here. passlib's bcrypt already caps input at 72 bytes,
    so removing the bound changes no observable timing or behaviour today; the
    bound guards a future hasher that doesn't cap, and keeps myMeal consistent
    with both siblings. This test only pins the clean-401 behaviour."""
    _register(app.test_client())
    r = app.test_client().post("/api/v1/users/login",
                               json={"username": "real@t.com", "password": "a" * 100000})
    assert r.status_code == 401
