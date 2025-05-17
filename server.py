from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uuid
import json
import random
import logging
import asyncio
import redis.asyncio as redis

logging.basicConfig(level=logging.INFO)

app = FastAPI()
rdb = redis.Redis(host="localhost", port=6379, decode_responses=True)  # Redis接続

connected_sockets = {}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    logging.info("[CONNECT] WebSocket 接続開始")
    await websocket.accept()

    try:
        init_message = await websocket.receive_text()
        init_data = json.loads(init_message)

        user_id = init_data.get("user_id")
        name = init_data.get("name")
        print(f"[INIT] user_id:{user_id}, name:{name}")

        await rdb.hset(f"user:{user_id}", mapping={
            "name": name,
            "status": "waiting",
            "opponent": ""
        })
        connected_sockets[user_id] = websocket

        # 再接続時の通知
        opponent_id = await rdb.hget(f"user:{user_id}", "opponent")
        if opponent_id:
            opponent_socket = connected_sockets.get(opponent_id)
            if opponent_socket:
                await opponent_socket.send_text(json.dumps({
                    "type": "opponent_reconnected"
                }))

        while True:
            message = await websocket.receive_text()
            data = json.loads(message)

            if data.get("type") == "register":
                await rdb.hset(f"user:{user_id}", mapping={
                    "name": name,
                    "status": "waiting"
                })
                asyncio.create_task(try_match(user_id))

            elif data.get("type") == "move":
                opponent_id = await rdb.hget(f"user:{user_id}", "opponent")
                if opponent_id in connected_sockets:
                    await connected_sockets[opponent_id].send_text(json.dumps({
                        "type": "move",
                        "x": data["x"],
                        "y": data["y"]
                    }))

    except WebSocketDisconnect:
        logging.info(f"[DISCONNECT] {user_id} が切断されました")
        await handle_disconnect(user_id)

async def try_match(current_id):
    all_keys = await rdb.keys("user:*")
    waiting_users = []
    for key in all_keys:
        uid = key.split(":")[1]
        status = await rdb.hget(key, "status")
        if status == "waiting":
            waiting_users.append(uid)

    if len(waiting_users) < 2:
        return

    user1_id = current_id
    user2_id = next((uid for uid in waiting_users if uid != user1_id), None)
    if not user2_id:
        return

    user1_name = await rdb.hget(f"user:{user1_id}", "name")
    user2_name = await rdb.hget(f"user:{user2_id}", "name")

    # 色と先手をランダムに
    colors = ["black", "white"]
    random.shuffle(colors)
    first_turn = "black"

    await rdb.hset(f"user:{user1_id}", mapping={"status": "matched", "opponent": user2_id})
    await rdb.hset(f"user:{user2_id}", mapping={"status": "matched", "opponent": user1_id})

    print(f"[MATCH] {user1_id} vs {user2_id}")

    await asyncio.sleep(2.0)

    await connected_sockets[user1_id].send_text(json.dumps({
        "type": "start_game",
        "your_color": colors[0],
        "opponent_name": user2_name,
        "first_turn": first_turn
    }))
    await connected_sockets[user2_id].send_text(json.dumps({
        "type": "start_game",
        "your_color": colors[1],
        "opponent_name": user1_name,
        "first_turn": first_turn
    }))

async def handle_disconnect(user_id):
    opponent_id = await rdb.hget(f"user:{user_id}", "opponent")
    await rdb.delete(f"user:{user_id}")
    connected_sockets.pop(user_id, None)

    if opponent_id and opponent_id in connected_sockets:
        try:
            await connected_sockets[opponent_id].send_text(json.dumps({
                "type": "opponent_disconnected"
            }))
            await rdb.hset(f"user:{opponent_id}", mapping={
                "status": "waiting",
                "opponent": ""
            })
        except:
            pass
