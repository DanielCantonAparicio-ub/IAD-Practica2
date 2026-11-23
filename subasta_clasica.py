"""Subasta holandesa de pescado con agentes osBrain (apartados 3.1–3.3)."""

import argparse
import csv
from collections import deque
from datetime import datetime
from math import isfinite
from pathlib import Path
from random import Random
from time import monotonic, sleep

from osbrain import Agent, run_agent, run_nameserver


# Tipo: (precio inicial, precio mínimo). H: merluza, S: lenguado, T: atún.
PRICES = {"H": (32, 12), "S": (28, 10), "T": (38, 15)}


class Operator(Agent):
    """Anuncia un pescado cada vez y adjudica al primer BUY válido."""

    def on_init(self):
        self.bind("PUB", alias="offers", serializer="json")
        self.bind("PULL", alias="orders", handler="receive_order", serializer="json")
        self.ready_merchants = set()
        self.sales = []
        self.current = None
        self.finished = False

    def configure_auction(self, operator_id, products, merchant_ids, interval, step):
        self.operator_id = operator_id
        self.products = deque(products)
        self.merchant_ids = set(merchant_ids)
        self.interval = interval
        self.step = step

    def check_subscriptions(self):
        if self.ready_merchants == self.merchant_ids:
            return True
        self.send("offers", {"operator_id": self.operator_id, "msg": "SYNC"})
        return False

    def open_next_product(self):
        if not self.products:
            self.finished = True
            return

        product_id, product_type = self.products.popleft()
        self.current = {
            "operator_id": self.operator_id,
            "product_id": product_id,
            "product_type": product_type,
            "price": PRICES[product_type][0],
        }
        self.publish_offer()

    def publish_offer(self):
        self.deadline = monotonic() + self.interval
        self.send("offers", self.current)
        self.after(
            self.interval,
            "expire_offer",
            self.current["product_id"],
            self.current["price"],
            alias="offer_timeout",
        )

    def is_current_offer(self, product_id, price):
        return (
            self.current is not None
            and product_id == self.current["product_id"]
            and price == self.current["price"]
        )

    def expire_offer(self, product_id, price):
        # Un temporizador antiguo puede haber entrado ya en la cola del agente.
        if not self.is_current_offer(product_id, price):
            return
        self.stop_timer("offer_timeout")
        minimum = PRICES[self.current["product_type"]][1]
        if price == minimum:
            self.close_product()
        else:
            self.current["price"] = max(minimum, price - self.step)
            self.publish_offer()

    def receive_order(self, order):
        merchant_id = order["merchant_id"]
        if (
            order["operator_id"] != self.operator_id
            or merchant_id not in self.merchant_ids
        ):
            return
        if order["msg"] == "READY":
            self.ready_merchants.add(merchant_id)
            return
        if (
            order["msg"] == "BUY"
            and self.is_current_offer(order["product_id"], order["price"])
            and monotonic() < self.deadline
        ):
            self.stop_timer("offer_timeout")
            self.close_product(merchant_id)

    def close_product(self, merchant_id=None):
        result = {
            **self.current,
            "merchant_id": merchant_id,
            "msg": "UNSOLD" if merchant_id is None else "SOLD",
        }
        if merchant_id is None:
            result["price"] = 0
        self.sales.append(result)
        self.current = None
        self.send("offers", result)
        self.open_next_product()


class Merchant(Agent):
    """Prioriza el surtido y aprovecha ofertas de su pescado favorito."""

    def on_init(self):
        self.stock = dict.fromkeys(PRICES, 0)
        self.pending = {}
        self.closed_products = set()

    def configure_merchant(self, merchant_id, preference, budget):
        self.merchant_id = merchant_id
        self.preference = preference
        self.balance = budget

    def receive_offer(self, offer):
        operator_id = offer["operator_id"]
        order_socket = f"orders-{operator_id}"
        message_type = offer.get("msg")
        if message_type == "SYNC":
            self.send(order_socket, {
                "operator_id": operator_id,
                "merchant_id": self.merchant_id,
                "msg": "READY",
            })
            return

        product_id = offer["product_id"]
        product_type = offer["product_type"]
        if product_id in self.closed_products:
            return
        # Una nueva rebaja indica que la solicitud anterior ya no puede prosperar.
        self.pending.pop(product_id, None)
        if message_type in ("SOLD", "UNSOLD"):
            self.closed_products.add(product_id)
            if offer["merchant_id"] == self.merchant_id:
                self.balance -= offer["price"]
                self.stock[product_type] += 1
            return

        price = offer["price"]
        if self.should_buy(product_type, price):
            self.pending[product_id] = (product_type, price)
            self.send(order_socket, {
                "operator_id": operator_id,
                "product_id": product_id,
                "merchant_id": self.merchant_id,
                "price": price,
                "msg": "BUY",
            })

    def should_buy(self, product_type, price):
        available = self.balance - sum(cost for _, cost in self.pending.values())
        if price > available:
            return False

        missing = self.stock[product_type] == 0
        favorite = product_type == self.preference
        if not missing and not favorite:
            return False
        if missing and any(kind == product_type for kind, _ in self.pending.values()):
            return False

        # Conserva al menos el precio mínimo de los otros tipos que faltan.
        reserve = sum(
            minimum
            for kind, (_, minimum) in PRICES.items()
            if kind != product_type and self.stock[kind] == 0
        )
        opening, minimum = PRICES[product_type]
        spread = opening - minimum
        if missing:
            premium = 2 * spread // 3 if favorite else spread // 2
            threshold = minimum + premium
            spending_limit = max(minimum, available - reserve)
        else:
            threshold = minimum + spread // 3
            spending_limit = available - reserve
        return price <= min(threshold, spending_limit)


def create_market(args):
    rng = Random(args.seed)
    budgets = args.budgets if args.budgets is not None else [100] * args.merchants
    setup = [
        (merchant_id, rng.choice(tuple(PRICES)), budget)
        for merchant_id, budget in enumerate(budgets, start=1)
    ]

    inventory = []
    product_id = 1
    for _ in range(args.operators):
        count = rng.randint(*args.fish_range) if args.fish_range else args.fish_per_operator
        products = []
        for _ in range(count):
            products.append((product_id, rng.choice(tuple(PRICES))))
            product_id += 1
        inventory.append(products)
    return setup, inventory


def wait_until(condition, timeout, error_message):
    deadline = monotonic() + timeout
    while not condition():
        if monotonic() >= deadline:
            raise RuntimeError(error_message)
        sleep(0.02)


def simulate(setup, inventory, interval, step):
    ns = run_nameserver()
    operators = []
    channels = []
    merchants = []
    try:
        merchant_ids = [merchant_id for merchant_id, _, _ in setup]
        for operator_id, products in enumerate(inventory, start=1):
            operator = run_agent(f"operator-{operator_id}", nsaddr=ns.addr(), base=Operator)
            operator.configure_auction(operator_id, products, merchant_ids, interval, step)
            operators.append(operator)
            channels.append((operator_id, operator.addr("offers"), operator.addr("orders")))

        for merchant_id, preference, budget in setup:
            merchant = run_agent(f"merchant-{merchant_id}", nsaddr=ns.addr(), base=Merchant)
            merchant.configure_merchant(merchant_id, preference, budget)
            for operator_id, offers, orders in channels:
                merchant.connect(orders, alias=f"orders-{operator_id}")
                merchant.connect(offers, handler="receive_offer")
            merchants.append(merchant)

        wait_until(
            lambda: all([operator.check_subscriptions() for operator in operators]),
            5,
            "No se pudieron sincronizar todas las suscripciones de los comerciantes.",
        )
        for operator in operators:
            operator.open_next_product()

        max_rounds = max(
            (opening - minimum + step - 1) // step + 1
            for opening, minimum in PRICES.values()
        )
        timeout = 5 + max(map(len, inventory)) * max_rounds * (interval + 0.05)
        wait_until(
            lambda: all(operator.get_attr("finished") for operator in operators),
            timeout,
            "La subasta no terminó dentro del tiempo previsto.",
        )
        total_products = sum(map(len, inventory))
        wait_until(
            lambda: all(len(merchant.get_attr("closed_products")) == total_products
                        for merchant in merchants),
            5,
            "Algún comerciante no recibió todos los resultados.",
        )
        sales = [result for operator in operators for result in operator.get_attr("sales")]
        return sorted(sales, key=lambda result: result["product_id"])
    finally:
        ns.shutdown()


def write_csv(path, headers, rows):
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(headers)
        writer.writerows(rows)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operators", type=int, default=2, help="número de operadores (2)")
    parser.add_argument("--merchants", type=int, default=3, help="número de comerciantes (3)")
    fish = parser.add_mutually_exclusive_group()
    fish.add_argument("--fish-per-operator", type=int, default=8, help="pescados por operador (8)")
    fish.add_argument("--fish-range", nargs=2, type=int, metavar=("MIN", "MAX"))
    parser.add_argument("--budgets", nargs="+", type=int, metavar="N",
                        help="un presupuesto por comerciante (100 para cada uno por defecto)")
    parser.add_argument("--interval", type=float, default=0.15, help="segundos por precio (0.15)")
    parser.add_argument("--step", type=int, default=3, help="rebaja por ronda (3)")
    parser.add_argument("--seed", type=int, help="semilla para preferencias y oferta de pescado")
    parser.add_argument("--output-dir", type=Path, default=Path.cwd(), help="directorio de los CSV")
    args = parser.parse_args()

    if min(args.operators, args.merchants, args.fish_per_operator, args.step) < 1:
        parser.error("el número de operadores, comerciantes, pescados y la rebaja deben ser positivos")
    if args.fish_range and not 1 <= args.fish_range[0] <= args.fish_range[1]:
        parser.error("--fish-range requiere 1 <= MIN <= MAX")
    if args.budgets is not None and (
        len(args.budgets) != args.merchants or min(args.budgets) < 0
    ):
        parser.error("--budgets requiere un presupuesto no negativo por comerciante")
    if not isfinite(args.interval) or args.interval <= 0:
        parser.error("--interval debe ser un número positivo y finito")
    return args


def main():
    args = parse_args()
    setup, inventory = create_market(args)
    sales = simulate(setup, inventory, args.interval, args.step)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
    setup_path = args.output_dir / f"setup {timestamp}.csv"
    log_path = args.output_dir / f"log {timestamp}.csv"
    write_csv(setup_path, ("Merchant", "Preference", "Budget"), setup)
    write_csv(log_path, ("Product", "Type", "Sale Price", "Merchant"), (
        (sale["product_id"], sale["product_type"], sale["price"], sale["merchant_id"])
        for sale in sales
    ))
    sold = sum(sale["msg"] == "SOLD" for sale in sales)
    print(f"Vendidos {sold} de {len(sales)} pescados.")
    print(setup_path)
    print(log_path)


if __name__ == "__main__":
    main()
