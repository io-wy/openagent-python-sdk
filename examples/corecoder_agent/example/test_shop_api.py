from fastapi.testclient import TestClient

from examples.corecoder_agent.example import shop_api
from examples.corecoder_agent.example.seed import reset_data, run_seed


client = TestClient(shop_api.app)


def setup_function() -> None:
    reset_data()


def auth_headers(token: str) -> dict[str, str]:
    return {"X-Token": token}


def test_seed_and_health() -> None:
    run_seed()
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_list_products_and_categories() -> None:
    run_seed()
    assert client.get("/categories").status_code == 200
    assert len(client.get("/categories").json()) == 2
    assert len(client.get("/products").json()) == 3


def test_cart_and_checkout_flow() -> None:
    run_seed()
    customer = shop_api.users[1]
    address = shop_api.addresses[0]
    product = shop_api.products[0]

    add_resp = client.post(
        "/cart/items",
        json={"product_id": product.id, "quantity": 2},
        headers=auth_headers(customer.token),
    )
    assert add_resp.status_code == 200

    order_resp = client.post(
        "/orders",
        json={"address_id": address.id},
        headers=auth_headers(customer.token),
    )
    assert order_resp.status_code == 201
    assert order_resp.json()["status"] == "created"


def test_order_state_machine() -> None:
    run_seed()
    admin = shop_api.users[0]
    customer = shop_api.users[1]
    address = shop_api.addresses[0]
    product = shop_api.products[0]

    client.post(
        "/cart/items",
        json={"product_id": product.id, "quantity": 1},
        headers=auth_headers(customer.token),
    )
    order = client.post(
        "/orders",
        json={"address_id": address.id},
        headers=auth_headers(customer.token),
    ).json()

    paid = client.patch(f"/orders/{order['id']}/pay", headers=auth_headers(customer.token))
    assert paid.status_code == 200
    assert paid.json()["status"] == "paid"

    shipped = client.patch(f"/orders/{order['id']}/ship", headers=auth_headers(admin.token))
    assert shipped.status_code == 200
    assert shipped.json()["status"] == "shipped"

    completed = client.patch(f"/orders/{order['id']}/complete", headers=auth_headers(admin.token))
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
