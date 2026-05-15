from __future__ import annotations

from examples.corecoder_agent.example.shop_api import (
    AddressCreate,
    CategoryCreate,
    ProductCreate,
    UserCreate,
    addresses,
    carts,
    categories,
    create_address,
    create_category,
    create_product,
    create_user,
    products,
    users,
)


def reset_data() -> None:
    users.clear()
    products.clear()
    categories.clear()
    addresses.clear()
    carts.clear()


def run_seed() -> None:
    reset_data()

    admin = create_user(UserCreate(email="admin@example.com", full_name="Admin", role="admin"))
    customer = create_user(UserCreate(email="customer@example.com", full_name="Customer", role="customer"))

    electronics = create_category(CategoryCreate(name="Electronics"), admin)
    books = create_category(CategoryCreate(name="Books"), admin)

    create_product(
        ProductCreate(
            name="Wireless Mouse",
            description="2.4G ergonomic mouse",
            price=19.9,
            stock=100,
            category_id=electronics.id,
            is_active=True,
        ),
        admin,
    )
    create_product(
        ProductCreate(
            name="Mechanical Keyboard",
            description="Blue switch keyboard",
            price=69.0,
            stock=50,
            category_id=electronics.id,
            is_active=True,
        ),
        admin,
    )
    create_product(
        ProductCreate(
            name="Clean Code",
            description="A handbook of agile software craftsmanship",
            price=39.0,
            stock=30,
            category_id=books.id,
            is_active=True,
        ),
        admin,
    )

    create_address(
        AddressCreate(
            recipient="Customer",
            line1="123 Example Street",
            city="Shanghai",
            country="CN",
            postal_code="200000",
        ),
        customer,
    )

    print("Seed completed.")
    print(f"Admin token: {admin.token}")
    print(f"Customer token: {customer.token}")


if __name__ == "__main__":
    run_seed()
