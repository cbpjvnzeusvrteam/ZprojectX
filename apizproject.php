<?php
header("Access-Control-Allow-Origin: *");
header("Content-Type: application/json");

$storage_file = "group-idchat.json";
$data = file_exists($storage_file) ? json_decode(file_get_contents($storage_file), true) : ["users" => [], "groups" => []];

$input = file_get_contents("php://input");
$payload = json_decode($input, true);

if (!isset($payload["id"]) || !isset($payload["type"])) {
    echo json_encode(["status" => "error", "message" => "Thiếu id hoặc type."]);
    exit;
}

$id = $payload["id"];
$type = $payload["type"];

if ($type === "private") {
    if (!in_array($id, $data["users"])) {
        $data["users"][] = $id;
    }
} elseif (in_array($type, ["group", "supergroup"])) {
    foreach ($data["groups"] as $g) {
        if ($g["id"] === $id) {
            echo json_encode(["status" => "ok", "message" => "Đã tồn tại"]);
            exit;
        }
    }
    $data["groups"][] = [
        "id" => $id,
        "title" => $payload["title"] ?? "",
        "username" => $payload["username"] ?? ""
    ];
}

file_put_contents($storage_file, json_encode($data, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE));
echo json_encode(["status" => "ok"]);