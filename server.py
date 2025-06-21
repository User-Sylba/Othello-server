from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uuid
import json
import random
import logging
import asyncio
import redis.asyncio as redis
import os
from dotenv import load_dotenv

load_dotenv()  # .env を読み込む

redis_url = os.getenv("REDIS_URL")
rdb = redis.from_url(redis_url, decode_responses=True)

logging.basicConfig(level=logging.INFO)

app = FastAPI()


connected_sockets = {}

def save_board():
    board = [[0] * 8 for _ in range(8)]
    mid = 4
    board[mid - 1][mid - 1] = -1
    board[mid][mid] = -1
    board[mid - 1][mid] = 1
    board[mid][mid - 1] = 1
    return board

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    logging.info("[CONNECT] WebSocket 接続開始")
            # 再接続時に盤面・ターンを復元送信
    
    await websocket.accept()
    user_id=None

    try:
        init_message = await websocket.receive_text()
        init_data = json.loads(init_message)

        user_id = init_data.get("user_id")
        name = init_data.get("name")
        print(f"[INIT] user_id:{user_id}, name:{name}")

        connected_sockets[user_id] = websocket

        board_data = await rdb.get(f"board:{user_id}")
        turn = await rdb.get(f"turn:{user_id}")
        color = await rdb.hget(f"user:{user_id}", "color")

        if board_data and turn and color:
            await websocket.send_text(json.dumps({
                "type": "restore_board",
                "board": json.loads(board_data),
                "current_player": 1 if turn == "black" else -1,
                "your_color": color
            }))
        

        existing_status = await rdb.hget(f"user:{user_id}","status")
        if existing_status is None:
            await rdb.hset(f"user:{user_id}", mapping={
                "name": name,
                "status": "waiting",
                "opponent": ""
        })
        
        # 再接続時の通知
        opponent_id = await rdb.hget(f"user:{user_id}", "opponent")
        if opponent_id:
            opponent_socket = connected_sockets.get(opponent_id)
            if opponent_socket:
                try:
                    await opponent_socket.send_text(json.dumps({
                        "type": "opponent_reconnected"
                    }))
                except Exception as e:
                    logging.warning(f"[WARN] opponent_reconnected の送信失敗: {e}")

        while True:
            
            message = await websocket.receive_text()
            data = json.loads(message)

            if data.get("type") == "register":
                user_id = data.get("user_id")
                name = data.get("name")
                connected_sockets[user_id] = websocket
                current_status = await rdb.hget(f"user:{user_id}", "status")

                if current_status == "matched":
                    print(f"[WARN] register 経由で matched ユーザーが再接続しようとしています（無視）")
                    await rdb.hset(f"user:{user_id}", "status", "waiting")
                

    # 通常の新規マッチング登録
                await rdb.hset(f"user:{user_id}", mapping={
                    "name": name,
                    "status": "waiting"
                })
                asyncio.create_task(try_match(user_id))

            if data.get("type") == "restore_request":    
                user_id = data.get("user_id") 
                print(f"[RESTORE_REQUEST] from user_id: {user_id}")
                connected_sockets[user_id] = websocket
    
                board_data = await rdb.get(f"board:{user_id}")
                turn = await rdb.get(f"turn:{user_id}")
                color = await rdb.hget(f"user:{user_id}", "color")
                opponent_id = await rdb.hget(f"user:{user_id}", "opponent")

                print(f"[RESTORE] user_id={user_id}")
                print(f"[RESTORE] opponent_id={opponent_id}")
                print(f"[RESTORE] connected_sockets.keys()={list(connected_sockets.keys())}")

                if board_data and turn and color:
                    opponent_id = await rdb.hget(f"user:{user_id}", "opponent")
                    opponent_name = None
                    if opponent_id:
                        opponent_name = await rdb.hget(f"user:{opponent_id}", "name")
                        if not opponent_name:
                            print(f"[ERROR] opponent_name が取得できません: opponent_id={opponent_id}")
                    
                        await websocket.send_text(json.dumps({
                            "type": "restore_board",
                            "board": json.loads(board_data),
                            "current_player": 1 if turn == "black" else -1,
                            "your_color": 1 if color == "black" else -1,
                            "your_turn":(turn == color),
                            "opponent_name": opponent_name,
                            "reconnect_code": True
                        }))
                        print(f"[RESTORE] Sent restore_board to {user_id}")
                else:
                    print(f"[RESTORE] board_dataなし: user_id={user_id}")

        # ✅ 対戦相手に再接続したことを通知
                   
                    if opponent_id in connected_sockets:
                        try:
                            await connected_sockets[opponent_id].send_text(json.dumps({
                               "type": "opponent_reconnected",
                               "user_id": user_id
                            }))
                            print(f"[RESTORE] Notified opponent {opponent_id} about {user_id}'s reconnection")

            # 🔁 相手側にも最新盤面を送信
                            opponent_turn = await rdb.get(f"turn:{opponent_id}")
                            opponent_color = await rdb.hget(f"user:{opponent_id}", "color")
                            opponent_board_data = await rdb.get(f"board:{opponent_id}")
                            if opponent_turn and opponent_color and opponent_board_data:
                                await connected_sockets[opponent_id].send_text(json.dumps({
                                    "type": "update_board",
                                    "board": json.loads(opponent_board_data),
                                    "current_player": 1 if opponent_turn == "black" else -1,
                                    
                                }))
                                print(f"[RESTORE] Sent updated board to opponent {opponent_id}")

                        except Exception as e:
                            print(f"[WARN] Failed to notify opponent or send board: {e}")

            
                
            elif data.get("type") == "move":
                x = data["x"]
                y = data["y"]

                opponent_id = await rdb.hget(f"user:{user_id}", "opponent")
                my_color = await rdb.hget(f"user:{user_id}", "color")
                opponent_color = "black" if my_color == "white" else "white"

    # 現在の board を取得し、反転処理
                board_data = await rdb.get(f"board:{user_id}")
                board = json.loads(board_data) if board_data else [[0]*8 for _ in range(8)]
                color_value = 1 if my_color == "black" else -1

    # 石を置いて、反転処理を実行
                def place_stone(board, x, y, color):
                    directions = [(-1, -1), (-1, 0), (-1, 1),
                                  (0, -1),          (0, 1),
                                  (1, -1),  (1, 0), (1, 1)]
                    flipped = []

                    if board[x][y] != 0:
                        return board  # 無効な位置

                    board[x][y] = color
                    for dx, dy in directions:
                        nx, ny = x + dx, y + dy
                        temp = []
                        while 0 <= nx < 8 and 0 <= ny < 8 and board[nx][ny] == -color:
                            temp.append((nx, ny))
                            nx += dx
                            ny += dy
                        if 0 <= nx < 8 and 0 <= ny < 8 and board[nx][ny] == color:
                            for fx, fy in temp:
                                board[fx][fy] = color
                                flipped.append((fx, fy))
                    return board

                board = place_stone(board, x, y, color_value)

    # 次のターンを決定
                current_turn = await rdb.get(f"turn:{user_id}")
                if not current_turn:
                    current_turn = "black"
                next_turn = "white" if current_turn == "black" else "black"

    # Redisに保存（再接続対応）
                await rdb.set(f"board:{user_id}", json.dumps(board), ex=3600)
                await rdb.set(f"board:{opponent_id}", json.dumps(board), ex=3600)
                await rdb.set(f"turn:{user_id}", next_turn, ex=3600)
                await rdb.set(f"turn:{opponent_id}", next_turn, ex=3600)

    # 両者に座標と色、次のターンを通知（board は送らない）
                for uid, color, socket in [
                    (user_id, my_color, connected_sockets.get(user_id)),
                    (opponent_id, opponent_color, connected_sockets.get(opponent_id))
                ]:
                    if socket:
                        await socket.send_text(json.dumps({
                            "type": "move",
                            "x": x,
                            "y": y,
                            "color": my_color,
                            "next_turn": next_turn,
                            "your_color": "black" if my_color == 1 else "white",
                            "your_turn": (next_turn == color)
                        }))

            elif data.get("type") == "pass":
                opponent_id = await rdb.hget(f"user:{user_id}", "opponent")

    # 現在の board を取得
                board_data = await rdb.get(f"board:{user_id}")
                board = json.loads(board_data) if board_data else [[0]*8 for _ in range(8)]

    # パス回数記録
                my_passed = await rdb.get(f"pass:{user_id}")
                opponent_passed = await rdb.get(f"pass:{opponent_id}")

                if my_passed == "true" and opponent_passed == "true":
                    print("[INFO] 両者が連続でパスしました。ゲーム終了処理を開始します。")
                    for uid in [user_id, opponent_id]:
                        if uid in connected_sockets:
                            await connected_sockets[uid].send_text(json.dumps({
                                "type": "end_game",
                                "board": board,
                                "your_color": await rdb.hget(f"user:{uid}", "color")
                            }))
                    return
                await rdb.set(f"pass:{user_id}", "true", ex=40)

                current_turn = await rdb.get(f"turn:{user_id}")
                if not current_turn:
                    current_turn = "black"
                next_turn = "white" if current_turn == "black" else "black"

    # 保存（再接続用）
                await rdb.set(f"board:{user_id}", json.dumps(board), ex=3600)
                await rdb.set(f"board:{opponent_id}", json.dumps(board), ex=3600)
                await rdb.set(f"turn:{user_id}", next_turn, ex=3600)
                await rdb.set(f"turn:{opponent_id}", next_turn, ex=3600)

    # 相手にパス通知
                opponent_color = await rdb.hget(f"user:{opponent_id}", "color")
                if opponent_id in connected_sockets:
                    await connected_sockets[opponent_id].send_text(json.dumps({
                        "type": "pass",
                        "next_turn": next_turn,
                        "your_color": opponent_color,
                        "your_turn": (next_turn == opponent_color)
            }))
       
                    
            elif data.get("type") == "end_game":
                opponent_id = await rdb.hget(f"user:{user_id}", "opponent")

    # 再接続用に有効期限を延長
                await rdb.expire(f"board:{user_id}", 40)
                await rdb.expire(f"board:{opponent_id}", 40)
                await rdb.expire(f"turn:{user_id}", 40)
                await rdb.expire(f"turn:{opponent_id}", 40)

    # Redisから取得（受信ではなく）
                board_data = await rdb.get(f"board:{user_id}")
                turn = await rdb.get(f"turn:{user_id}")
                board = json.loads(board_data) if board_data else [[0]*8 for _ in range(8)]

                my_color = await rdb.hget(f"user:{user_id}", "color")
                opponent_color = "black" if my_color == "white" else "white"

    # 状態を waiting に戻す
                await rdb.hset(f"user:{user_id}", mapping={"status": "waiting", "opponent": ""})
                await rdb.hset(f"user:{opponent_id}", mapping={"status": "waiting", "opponent": ""})

                for uid, color, socket in [
                     (user_id, my_color, connected_sockets.get(user_id)),
                     (opponent_id, opponent_color, connected_sockets.get(opponent_id))
                ]:
                    if socket:
                        try:
                            opponent_name = await rdb.hget(f"user:{opponent_id if uid == user_id else user_id}", "name")
                            await socket.send_text(json.dumps({
                                "type": "end_game",
                                "board": board,
                                "current_player": 1 if turn == "black" else -1,
                                "your_color": color,
                                "opponent_name":opponent_name
                            }))
                        except Exception as e:
                            logging.warning(f"[WARN] end_game 送信失敗: {e}")

                

    except WebSocketDisconnect:
        logging.info(f"[DISCONNECT] {user_id} が切断されました")
        await handle_disconnect(user_id)

async def try_match(current_id):
    print(f"[DEBUG] try_match called for {current_id}")
    print(f"[DEBUG] waiting_users =", waiting_users)
    
    all_keys = await rdb.keys("user:*")
    waiting_users = []
    for key in all_keys:
        uid = key.split(":")[1]
        status = await rdb.hget(key, "status")
        if status == "waiting":
            waiting_users.append(uid)

    if len(waiting_users) < 2:
        return

    # 2人をランダムに選ぶ
    candidates = [uid for uid in waiting_users if uid != current_id]
    if not candidates:
        return
    
    opponent_id = random.choice(candidates)
    user_ids = [current_id, opponent_id]
    random.shuffle(user_ids)
    user1_id, user2_id = user_ids[0], user_ids[1]

    user1_name = await rdb.hget(f"user:{user1_id}", "name")
    user2_name = await rdb.hget(f"user:{user2_id}", "name")

    colors = ["black", "white"]
    random.shuffle(colors)
    user1_color = "black"
    user2_color = "white"
    first_turn = "black"

    await rdb.hset(f"user:{user1_id}", mapping={
        "status": "matched",
        "opponent": user2_id,
        "color": user1_color,
        "opponent_name": user2_name
    })
    await rdb.hset(f"user:{user2_id}", mapping={
        "status": "matched",
        "opponent": user1_id,
        "color": user2_color,
        "opponent_name": user1_name
    })

    print(f"[MATCH] {user1_id} ({user1_color}) vs {user2_id} ({user2_color})")

    await asyncio.sleep(2.0)

    board = save_board()

    if user1_id in connected_sockets:
        await connected_sockets[user1_id].send_text(json.dumps({
            "type": "start_game",
            "your_color": user1_color,
            "opponent_name": user2_name,
            "first_turn": first_turn,
            "board": board
        }))
    else:
        logging.warning(f"[try_match] user1_id {user1_id} がconnected_socketsに存在しません")

    if user2_id in connected_sockets:
        await connected_sockets[user2_id].send_text(json.dumps({
            "type": "start_game",
            "your_color": user2_color,
            "opponent_name": user1_name,
            "first_turn": first_turn,
            "board": board
        }))
    else:
         logging.warning(f"[try_match] user2_id {user2_id} がconnected_socketsに存在しません")

    

    await rdb.set(f"board:{user1_id}", json.dumps(board), ex=3600)
    await rdb.set(f"board:{user2_id}", json.dumps(board), ex=3600)
    await rdb.set(f"turn:{user1_id}", first_turn, ex=3600)
    await rdb.set(f"turn:{user2_id}", first_turn, ex=3600)

async def handle_disconnect(user_id):
    
    
    opponent_id = await rdb.hget(f"user:{user_id}", "opponent")

    # userデータを完全には消さず、40秒だけ保持
    await rdb.expire(f"user:{user_id}", 40)
    await rdb.expire(f"board:{user_id}", 40)
    await rdb.expire(f"turn:{user_id}", 40)

    connected_sockets.pop(user_id, None)

    if opponent_id and opponent_id in connected_sockets:
        try:
            await connected_sockets[opponent_id].send_text(json.dumps({
                "type": "opponent_disconnected"
            }))
        except:
            pass

        # 対戦相手も40秒後にクリーンアップできるように更新
        await rdb.expire(f"user:{opponent_id}", 40)
        await rdb.expire(f"board:{opponent_id}", 40)
        await rdb.expire(f"turn:{opponent_id}", 40)

        asyncio.create_task(wait_end(user_id, opponent_id))

async def wait_end(disconnect_id, opponent_id):
    await asyncio.sleep(40)
    if disconnect_id not in connected_sockets:
        print(f"[TIMEOUT]ユーザー {disconnect_id} が再接続しませんでした。")
        
        board_data = await rdb.get(f"board:{opponent_id}")
        turn = await rdb.get(f"turn:{opponent_id}")
        color = await rdb.hget(f"user:{opponent_id}", "color")

        if board_data and turn and color and opponent_id in connected_sockets:
            try:
                await connected_sockets[opponent_id].send_text(json.dumps({
                    "type": "end_game",
                    "board": json.loads(board_data),
                    "current_player": 1 if turn == "black" else -1,
                    "your_color": color,
                    
                }))
                print(f"[END_GAME] {opponent_id} に対戦終了を通知しました。")
            except Exception as e:
                print(f"[ERROR] end_game の送信失敗: {e}")

        await rdb.delete(f"user:{disconnect_id}")
        await rdb.delete(f"user:{opponent_id}")
        await rdb.delete(f"board:{disconnect_id}")
        await rdb.delete(f"board:{opponent_id}")
        await rdb.delete(f"turn:{disconnect_id}")
        await rdb.delete(f"turn:{opponent_id}")
        print(f"[CLEANUP] {disconnect_id} と {opponent_id} のデータを削除しました。")

    
