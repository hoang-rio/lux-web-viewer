# Lux Viewer

Tiếng Việt | [English](README.md)

[![GitHub Release](https://img.shields.io/github/v/release/hoang-rio/lux-web-viewer)](https://github.com/hoang-rio/lux-web-viewer/releases) [![License](https://img.shields.io/github/license/hoang-rio/lux-web-viewer?color=blue)](LICENSE) [![Commit activity](https://img.shields.io/github/commit-activity/m/hoang-rio/lux-web-viewer)](https://github.com/hoang-rio/lux-web-viewer/commits/main/) [![GitHub last commit](https://img.shields.io/github/last-commit/hoang-rio/lux-web-viewer?color=blue)](https://github.com/hoang-rio/lux-web-viewer)

[![Docker publish action status](https://img.shields.io/github/actions/workflow/status/hoang-rio/lux-web-viewer/docker-publish.yml?label=docker%20publish%20action)](https://github.com/hoang-rio/lux-web-viewer/actions/workflows/docker-publish.yml) [![Docker Image Latest](https://ghcr-badge.egpl.dev/hoang-rio/lux-web-viewer/latest_tag?trim=major&label=latest%20image%20tag)](https://github.com/hoang-rio/lux-web-viewer/pkgs/container/lux-web-viewer) [![Docker Image Latest](https://ghcr-badge.egpl.dev/hoang-rio/lux-web-viewer/tags?trim=major)](https://github.com/hoang-rio/lux-web-viewer/pkgs/container/lux-web-viewer) [![Docker Image Size](https://ghcr-badge.egpl.dev/hoang-rio/lux-web-viewer/size)](https://github.com/hoang-rio/lux-web-viewer/pkgs/container/lux-web-viewer)

## Giới thiệu
<p align="center">
    <img src="misc/lux-viewer-logo.png" alt="Lux Viewer Logo"/>
</p>
Một script Python cho phép giám sát biến tần Luxpower SNA theo thời gian thực. Đã thử nghiệm trên Luxpower SNA 6K với kết nối wifi loại cũ (tên wifi dạng BAxxxxxx)

## Thiết lập biến tần
Xem hướng dẫn trên wiki của `lxp-bridge` [tại đây](https://github.com/celsworth/lxp-bridge/wiki/Inverter-Setup). _(Bạn có thể bỏ qua phần thiết lập sạc AC)_

## Cấu hình
* Sao chép `.env.example` thành `.env`
* Cập nhật thông tin cấu hình trong tập tin `.env` với thông tin của bạn

### Nền tảng đa khách hàng (PostgreSQL)

Nhánh này bao gồm nền tảng PostgreSQL + Alembic cho tính năng sắp tới hỗ trợ nhiều người dùng và nhiều biến tần.

Các biến `.env` mới bắt buộc:

* `POSTGRES_DB_URL` (ví dụ: `postgresql+psycopg://postgres:postgres@localhost:5432/lux_web_viewer`)
* `JWT_SECRET`, `JWT_ACCESS_EXPIRE_MINUTES`, `JWT_REFRESH_EXPIRE_DAYS`
* `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_TLS`, `SMTP_SSL`

Chạy migration ban đầu:

* `alembic upgrade head`

### Chế độ ReadInput (DONGLE/SERVER)
Bạn có thể chọn loại frame input cần đọc bằng biến `READ_INPUT_MODE` trong `.env`:

* `READ_INPUT_MODE=INPUT1` (aliases: `INPUT1`, `READINPUT1`, `READ_INPUT1`): chỉ đọc ReadInput1 (register `0`, count `40`)
* `READ_INPUT_MODE=ALL`: đọc tuần tự ReadInput1 -> ReadInput4 (register `0`, `40`, `80`, `120`)

`ALL` cho dữ liệu đầy đủ hơn bằng cách gộp ReadInput1-4, còn `INPUT1` có payload nhỏ hơn.

## Cài đặt và chạy
* Đồng bộ git submodule với `git submodule init && git submodule update`
* Yêu cầu Python 3
* Tạo môi trường ảo Python với `python -m venv venv`
* Kích hoạt môi trường ảo Python bằng `source venv/Scripts/activate` trên Windows dùng git-bash hoặc `source venv/bin/activate` trên Unix/Linux
* Cài đặt các thư viện phụ thuộc với `pip install -r requirements.txt` hoặc `./pip-binary-install.sh` trên thiết bị cấu hình yếu (ví dụ: bộ định tuyến OpenWrt)
* Chạy ứng dụng với `python app.py`
> Nếu bạn không thể cài đặt và chạy ứng dụng, bạn có thể sử dụng phương pháp chạy bằng docker bên dưới

## Muốn dùng docker? Đây là các bước
* Di chuyển vào thư mục `docker`
* Chạy lệnh `docker compose up -d` để chạy container docker

## Ứng dụng di động
Bạn có thể tự xây dựng ứng dụng thông báo cho Android/iOS và gửi Firebase device token vào tập tin `devices.json` để nhận thông báo mỗi khi trạng thái kết nối lưới điện thay đổi.

Tác giả cũng phát triển một ứng dụng **Lux App Viewer (Android/iOS)**. Nếu bạn cần, hãy liên hệ để nhận hỗ trợ.

Web server tích hợp cung cấp các API thân thiện với thiết bị di động:
* `POST /fcm/register` với JSON (hoặc form body) `token=<firebase_device_token>` để đăng ký token thiết bị
* `GET /mobile/state` để lấy trạng thái kết nối lưới hiện tại và lịch sử thay đổi

## Trình xem web
* Biên dịch giao diện với lệnh `cd web_viewer/fe_src && yarn install && yarn build` (Bỏ qua bước này nếu bạn chạy bằng docker)
* Bây giờ bạn có thể xem giao diện web LuxPower theo thời gian thực tại http://localhost:88 (hoặc ở cổng khác nếu bạn thay đổi `PORT` trong `.env`).
* HTTPS cũng được hỗ trợ; bật bằng cách đặt `HTTPS_ENABLED=true` và cung cấp `HTTPS_PORT`, `HTTPS_CERT_FILE`, `HTTPS_KEY_FILE` trong `.env`.

## Đọc/Ghi register Modbus
Ứng dụng có thể đọc và ghi register biến tần qua giao thức Modbus sử dụng chính kết nối dongle hiện có — không cần phần cứng hay cấu hình bổ sung. Ở chế độ SERVER, các yêu cầu Modbus được xen kẽ liền mạch với chu kỳ poll ReadInput nên việc giám sát thời gian thực vẫn chạy bình thường; ở chế độ DONGLE, yêu cầu được gửi trực tiếp tới dongle.

### Tính năng
* Đọc/ghi register Modbus (fc 3/4 đọc, fc 6/16 ghi) qua dongle
* Khoảng 60 cài đặt nhóm theo loại: Âm thanh còi, Ứng dụng, Sạc, Xả, Pin
* Bảng điều khiển quản trị (Cài đặt → Modbus) để duyệt, đọc và áp dụng giá trị register
* Giá trị đọc được lưu cache (mặc định 30 giây, `MODBUS_READ_CACHE_TTL`) và làm mới khi ghi; lệnh đọc an toàn (fc 3/4) được thử lại một lần khi hết thời gian chờ

### REST API
* `GET /modbus/status` — trạng thái controller
* `GET /modbus/registers` — danh mục register nhóm theo loại
* `GET /modbus/read?category=<key>` — đọc giá trị hiện tại (bỏ qua `category` để đọc tất cả)
* `POST /modbus/write` với JSON `{ "key": <mã register>, "value": <giá trị mới> }` để ghi register

Truy cập yêu cầu JWT hợp lệ và chỉ áp dụng cho inverter đã đăng ký của người dùng đã đăng nhập.

<center>
<picture style="max-width: 800px">
    <source srcset="misc/screenshot-light-vi.png" media="(prefers-color-scheme: light)"/>
    <source srcset="misc/screenshot-dark-vi.png"  media="(prefers-color-scheme: dark)"/>
    <img src="misc/screenshot-light-vi.png"/>
</picture>
</center>

## Giấy phép

Dự án này được phát hành theo giấy phép MIT. Xem chi tiết trong tập tin [LICENSE](LICENSE).

## Bên thứ ba

Cảm ơn [@celsworth](https://github.com/celsworth) với dự án tuyệt vời [celsworth/lxp-packet](https://github.com/celsworth/lxp-packet) *(đã bị xoá)* và [celsworth/lxp-bridge](https://github.com/celsworth/lxp-bridge) (theo giấy phép MIT)

Dự án này có chứa mã nguồn từ thư viện `aiohttp` (https://github.com/aio-libs/aiohttp.git) được phát hành dưới giấy phép Apache License 2.0.
