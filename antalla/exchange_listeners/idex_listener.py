import json
import logging
from datetime import datetime

from dateutil.parser import parse as parse_date
import websockets

from .. import settings
from .. import models
from .. import actions
from ..exchange_listener import ExchangeListener
from ..websocket_listener import WebsocketListener

@ExchangeListener.register("idex")
class IdexListener(WebsocketListener):
    def __init__(self, exchange, on_event, ws_url=settings.IDEX_WS_URL):
        super().__init__(exchange, on_event, ws_url)

    async def _send_message(self, websocket, request, payload, **kwargs):
        data = dict(request=request, payload=json.dumps(payload))
        data.update(kwargs)
        message = json.dumps(data)
        logging.debug("> %s: %s", request, payload)
        await websocket.send(message)
        response = await websocket.recv()
        logging.debug("< %s", response)
        return json.loads(response)

    async def _setup_connection(self, websocket):
        handshake_data = dict(version="1.0.0", key=settings.IDEX_API_KEY)
        handshake_res = await self._send_message(websocket, "handshake", handshake_data)
        subscription_data = dict(topics=settings.MARKETS, events=settings.IDEX_EVENTS)
        await self._send_message(websocket, "subscribeToMarkets",
                                 subscription_data, sid=handshake_res["sid"])

    def _parse_message(self, message):
        event, payload = message["event"], json.loads(message["payload"])
        func = getattr(self, f"_parse_{event}", None)
        if func:
            return func(payload)
        return []

    def _parse_market_orders(self, payload):
        buy_sym, sell_sym = payload["market"].split("_")
        orders = [self._convert_raw_order(order, buy_sym, sell_sym)
                  for order in payload["orders"]]
        return [actions.InsertAction(orders)]

    def _convert_raw_order(self, raw_order, buy_sym, sell_sym):
        return models.Order(
            timestamp=parse_date(raw_order["createdAt"]),
            exchange=self.exchange,
            buy_sym_id=buy_sym,
            sell_sym_id=sell_sym,
            price=float(raw_order["amountSell"])/float(raw_order["amountBuy"]),
            quantity=float(raw_order["amountBuy"]),
            side="buy",
            user=raw_order["user"],
            exchange_order_id=raw_order["hash"],
        )

    def _parse_market_cancels(self, payload):
        update_actions = []
        for cancel in payload["cancels"]:
                update_actions.append(actions.UpdateAction(
                    models.Order,
                    {"exchange_order_id": cancel["orderHash"], "exchange_id": self.exchange.id},
                    {"cancelled_at": parse_date(cancel["createdAt"])}
                ))
        return update_actions

    def _parse_market_trades(self, payload):  
        update_actions = []
        buy_sym, sell_sym = payload["market"].split("_")
        trades = []
        for trade in payload["trades"]:
            trades.append(self._convert_raw_trade(trade, buy_sym, sell_sym))
            update_actions.append(actions.UpdateAction(
                    models.Order,
                    {"exchange_order_id": trade["orderHash"], "exchange_id": self.exchange.id},
                    {"filled_at": datetime.fromtimestamp(trade["timestamp"])}
                ))
        insert_actions = [actions.InsertAction(trades)]
        return insert_actions + update_actions

    def _convert_raw_trade(self, raw_trade, buy_sym, sell_sym):
        return models.Trade(
            timestamp=datetime.fromtimestamp(raw_trade["timestamp"]),
            trade_type=raw_trade["type"],
            exchange=self.exchange,
            buy_sym_id=buy_sym,
            sell_sym_id=sell_sym,
            maker=raw_trade["maker"],
            taker=raw_trade["taker"],
            exchange_order_id=raw_trade["orderHash"],
            gas_fee=float(raw_trade["gasFee"]),
            price=float(raw_trade["price"]),
            amount=float(raw_trade["amount"]),
            total=float(raw_trade["total"]),
            buyer_fee=float(raw_trade["buyerFee"]),
            seller_fee=float(raw_trade["sellerFee"])
        )