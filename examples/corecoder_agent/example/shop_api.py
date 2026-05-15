from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, EmailStr, Field


app = FastAPI(title="Shop Backend API", version="2.0.0")


class UserRole(str, Enum):
    customer = "customer"
    admin = "admin"


class OrderStatus(str, Enum):
    created = "created"
    paid = "paid"
    shipped = "shipped"
    completed = "completed"
    cancelled = "cancelled"


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class Category(CategoryCreate):
    id: int


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    price: float = Field(gt=0)
    stock: int = Field(ge=0)
    category_id: int
    is_active: bool = True


class Product(ProductCreate):
    id: int


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=100)
    role: UserRole = UserRole.customer


class AddressCreate(BaseModel):
    recipient: str = Field(min_length=1, max_length=100)
    line1: str = Field(min_length=1, max_length=200)
    city: str = Field(min_length=1, max_length=100)
    country: str = Field(min_length=1, max_length=100)
    postal_code: str = Field(min_length=1, max_length=20)


class Address(AddressCreate):
    id: int
    user_id: int


class User(UserCreate):
    id: int
    token: str


class CartItemCreate(BaseModel):
    product_id: int
    quantity: int = Field(gt=0)


class CartItem(BaseModel):
    product_id: int
    quantity: int


class OrderItem(BaseModel):
    product_id: int
    quantity: int
    unit_price: float


class Order(BaseModel):
    id: int
    user_id: int
    address_id: int
    items: list[OrderItem]
    total_amount: float
    status: OrderStatus
    created_at: datetime


class CheckoutRequest(BaseModel):
    address_id: int


categories: list[Category] = []
products: list[Product] = []
users: list[User] = []
addresses: list[Address] = []
carts: dict[int, list[CartItem]] = {}
orders: list[Order] = []


async def get_current_user(x_token: Annotated[str | None, Header()] = None) -> User:
    if not x_token:
        raise HTTPException(status_code=401, detail="Missing X-Token header")
    for user in users:
        if user.token == x_token:
            return user
    raise HTTPException(status_code=401, detail="Invalid token")


async def get_admin_user(current_user: Annotated[User, Depends(get_current_user)]) -> User:
    if current_user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


def get_product_or_404(product_id: int) -> Product:
    for p in products:
        if p.id == product_id:
            return p
    raise HTTPException(status_code=404, detail="Product not found")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/users", response_model=User, status_code=201)
def create_user(payload: UserCreate) -> User:
    if any(u.email == payload.email for u in users):
        raise HTTPException(status_code=400, detail="Email already exists")
    user = User(id=len(users) + 1, token=f"token-{len(users) + 1}", **payload.model_dump())
    users.append(user)
    carts[user.id] = []
    return user


@app.get("/users/me", response_model=User)
def read_me(current_user: Annotated[User, Depends(get_current_user)]) -> User:
    return current_user


@app.post("/addresses", response_model=Address, status_code=201)
def create_address(
    payload: AddressCreate,
    current_user: Annotated[User, Depends(get_current_user)],
) -> Address:
    address = Address(id=len(addresses) + 1, user_id=current_user.id, **payload.model_dump())
    addresses.append(address)
    return address


@app.get("/addresses", response_model=list[Address])
def list_addresses(current_user: Annotated[User, Depends(get_current_user)]) -> list[Address]:
    return [a for a in addresses if a.user_id == current_user.id]


@app.post("/categories", response_model=Category, status_code=201)
def create_category(payload: CategoryCreate, _: Annotated[User, Depends(get_admin_user)]) -> Category:
    if any(c.name.lower() == payload.name.lower() for c in categories):
        raise HTTPException(status_code=400, detail="Category already exists")
    category = Category(id=len(categories) + 1, **payload.model_dump())
    categories.append(category)
    return category


@app.get("/categories", response_model=list[Category])
def list_categories() -> list[Category]:
    return categories


@app.post("/products", response_model=Product, status_code=201)
def create_product(payload: ProductCreate, _: Annotated[User, Depends(get_admin_user)]) -> Product:
    if not any(c.id == payload.category_id for c in categories):
        raise HTTPException(status_code=400, detail="Invalid category_id")
    product = Product(id=len(products) + 1, **payload.model_dump())
    products.append(product)
    return product


@app.get("/products", response_model=list[Product])
def list_products(
    q: str | None = None,
    category_id: int | None = None,
    is_active: bool | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[Product]:
    filtered = products
    if q:
        needle = q.lower()
        filtered = [p for p in filtered if needle in p.name.lower() or needle in p.description.lower()]
    if category_id is not None:
        filtered = [p for p in filtered if p.category_id == category_id]
    if is_active is not None:
        filtered = [p for p in filtered if p.is_active == is_active]
    return filtered[offset : offset + limit]


@app.get("/products/{product_id}", response_model=Product)
def get_product(product_id: int) -> Product:
    return get_product_or_404(product_id)


@app.put("/products/{product_id}", response_model=Product)
def update_product(
    product_id: int,
    payload: ProductCreate,
    _: Annotated[User, Depends(get_admin_user)],
) -> Product:
    if not any(c.id == payload.category_id for c in categories):
        raise HTTPException(status_code=400, detail="Invalid category_id")
    for idx, product in enumerate(products):
        if product.id == product_id:
            updated = Product(id=product_id, **payload.model_dump())
            products[idx] = updated
            return updated
    raise HTTPException(status_code=404, detail="Product not found")


@app.patch("/products/{product_id}/stock", response_model=Product)
def update_stock(
    product_id: int,
    stock: int = Query(ge=0),
    _: Annotated[User, Depends(get_admin_user)] = None,
) -> Product:
    for idx, product in enumerate(products):
        if product.id == product_id:
            updated = product.model_copy(update={"stock": stock})
            products[idx] = updated
            return updated
    raise HTTPException(status_code=404, detail="Product not found")


@app.delete("/products/{product_id}", status_code=204)
def delete_product(product_id: int, _: Annotated[User, Depends(get_admin_user)]) -> None:
    for idx, product in enumerate(products):
        if product.id == product_id:
            products.pop(idx)
            return None
    raise HTTPException(status_code=404, detail="Product not found")


@app.get("/cart", response_model=list[CartItem])
def get_cart(current_user: Annotated[User, Depends(get_current_user)]) -> list[CartItem]:
    return carts[current_user.id]


@app.post("/cart/items", response_model=list[CartItem])
def add_to_cart(
    payload: CartItemCreate,
    current_user: Annotated[User, Depends(get_current_user)],
) -> list[CartItem]:
    product = get_product_or_404(payload.product_id)
    if not product.is_active:
        raise HTTPException(status_code=400, detail="Product is inactive")

    user_cart = carts[current_user.id]
    for idx, item in enumerate(user_cart):
        if item.product_id == payload.product_id:
            new_qty = item.quantity + payload.quantity
            if new_qty > product.stock:
                raise HTTPException(status_code=400, detail="Insufficient stock")
            user_cart[idx] = CartItem(product_id=item.product_id, quantity=new_qty)
            return user_cart

    if payload.quantity > product.stock:
        raise HTTPException(status_code=400, detail="Insufficient stock")
    user_cart.append(CartItem(**payload.model_dump()))
    return user_cart


@app.patch("/cart/items/{product_id}", response_model=list[CartItem])
def update_cart_item(
    product_id: int,
    quantity: int = Query(gt=0),
    current_user: Annotated[User, Depends(get_current_user)] = None,
) -> list[CartItem]:
    product = get_product_or_404(product_id)
    if quantity > product.stock:
        raise HTTPException(status_code=400, detail="Insufficient stock")

    user_cart = carts[current_user.id]
    for idx, item in enumerate(user_cart):
        if item.product_id == product_id:
            user_cart[idx] = CartItem(product_id=product_id, quantity=quantity)
            return user_cart
    raise HTTPException(status_code=404, detail="Cart item not found")


@app.delete("/cart/items/{product_id}", response_model=list[CartItem])
def remove_from_cart(
    product_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
) -> list[CartItem]:
    user_cart = carts[current_user.id]
    carts[current_user.id] = [item for item in user_cart if item.product_id != product_id]
    return carts[current_user.id]


@app.post("/orders", response_model=Order, status_code=201)
def create_order(
    payload: CheckoutRequest,
    current_user: Annotated[User, Depends(get_current_user)],
) -> Order:
    user_cart = carts[current_user.id]
    if not user_cart:
        raise HTTPException(status_code=400, detail="Cart is empty")

    address = next((a for a in addresses if a.id == payload.address_id and a.user_id == current_user.id), None)
    if not address:
        raise HTTPException(status_code=404, detail="Address not found")

    order_items: list[OrderItem] = []
    total_amount = 0.0

    for cart_item in user_cart:
        product = get_product_or_404(cart_item.product_id)
        if not product.is_active:
            raise HTTPException(status_code=400, detail=f"Product {product.id} is inactive")
        if product.stock < cart_item.quantity:
            raise HTTPException(status_code=400, detail=f"Insufficient stock for product {product.id}")

    for cart_item in user_cart:
        product = get_product_or_404(cart_item.product_id)
        product.stock -= cart_item.quantity
        order_items.append(
            OrderItem(product_id=product.id, quantity=cart_item.quantity, unit_price=product.price)
        )
        total_amount += product.price * cart_item.quantity

    order = Order(
        id=len(orders) + 1,
        user_id=current_user.id,
        address_id=address.id,
        items=order_items,
        total_amount=round(total_amount, 2),
        status=OrderStatus.created,
        created_at=datetime.utcnow(),
    )
    orders.append(order)
    carts[current_user.id] = []
    return order


@app.get("/orders", response_model=list[Order])
def list_orders(current_user: Annotated[User, Depends(get_current_user)]) -> list[Order]:
    if current_user.role == UserRole.admin:
        return orders
    return [o for o in orders if o.user_id == current_user.id]


@app.get("/orders/{order_id}", response_model=Order)
def get_order(order_id: int, current_user: Annotated[User, Depends(get_current_user)]) -> Order:
    for order in orders:
        if order.id == order_id:
            if current_user.role != UserRole.admin and order.user_id != current_user.id:
                raise HTTPException(status_code=403, detail="Not allowed")
            return order
    raise HTTPException(status_code=404, detail="Order not found")


@app.patch("/orders/{order_id}/pay", response_model=Order)
def pay_order(order_id: int, current_user: Annotated[User, Depends(get_current_user)]) -> Order:
    for idx, order in enumerate(orders):
        if order.id == order_id:
            if current_user.role != UserRole.admin and order.user_id != current_user.id:
                raise HTTPException(status_code=403, detail="Not allowed")
            if order.status != OrderStatus.created:
                raise HTTPException(status_code=400, detail="Order cannot be paid")
            paid = order.model_copy(update={"status": OrderStatus.paid})
            orders[idx] = paid
            return paid
    raise HTTPException(status_code=404, detail="Order not found")


@app.patch("/orders/{order_id}/ship", response_model=Order)
def ship_order(order_id: int, _: Annotated[User, Depends(get_admin_user)]) -> Order:
    for idx, order in enumerate(orders):
        if order.id == order_id:
            if order.status != OrderStatus.paid:
                raise HTTPException(status_code=400, detail="Only paid orders can be shipped")
            shipped = order.model_copy(update={"status": OrderStatus.shipped})
            orders[idx] = shipped
            return shipped
    raise HTTPException(status_code=404, detail="Order not found")


@app.patch("/orders/{order_id}/complete", response_model=Order)
def complete_order(order_id: int, _: Annotated[User, Depends(get_admin_user)]) -> Order:
    for idx, order in enumerate(orders):
        if order.id == order_id:
            if order.status != OrderStatus.shipped:
                raise HTTPException(status_code=400, detail="Only shipped orders can be completed")
            completed = order.model_copy(update={"status": OrderStatus.completed})
            orders[idx] = completed
            return completed
    raise HTTPException(status_code=404, detail="Order not found")


@app.patch("/orders/{order_id}/cancel", response_model=Order)
def cancel_order(order_id: int, current_user: Annotated[User, Depends(get_current_user)]) -> Order:
    for idx, order in enumerate(orders):
        if order.id == order_id:
            if current_user.role != UserRole.admin and order.user_id != current_user.id:
                raise HTTPException(status_code=403, detail="Not allowed")
            if order.status not in (OrderStatus.created, OrderStatus.paid):
                raise HTTPException(status_code=400, detail="Order cannot be cancelled")

            for item in order.items:
                product = get_product_or_404(item.product_id)
                product.stock += item.quantity

            cancelled = order.model_copy(update={"status": OrderStatus.cancelled})
            orders[idx] = cancelled
            return cancelled
    raise HTTPException(status_code=404, detail="Order not found")
