# Day 12 Lab - Mission Answers

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns found
Trong file `01-localhost-vs-production/develop/app.py`, có các vấn đề anti-patterns sau:
1. **Hardcoded Secrets**: API Key (`OPENAI_API_KEY`) và database credentials (`DATABASE_URL`) được ghi trực tiếp vào mã nguồn. Nếu đẩy lên Git (GitHub, GitLab), các thông tin nhạy cảm này sẽ bị lộ ngay lập tức.
2. **Thiếu Quản lý Cấu hình (Violation of 12-Factor App)**: Các cấu hình như `DEBUG = True` và `MAX_TOKENS = 500` bị cố định (hardcode) trong code thay vì được tải từ environment variables.
3. **Ghi Log Unstructured bằng `print()`**: Sử dụng `print()` làm cản trở việc thu thập và phân tích log tự động (qua Datadog, ELK, Loki). Hơn nữa, chương trình in cả API key nhạy cảm ra stdout.
4. **Không có Health Check Endpoints**: Thiếu các endpoint `/health` và `/ready`. Các nền tảng Cloud (Railway, Render, v.v.) không thể giám sát trạng thái để tự động khởi động lại container khi bị lỗi hoặc treo.
5. **Cố định Host và Port**: Liên kết (bind) cố định tới `host="localhost"` và `port=8000`. Khi chạy trong Docker container, host phải là `0.0.0.0` mới có thể nhận request từ bên ngoài container. Port cũng cần cấu hình động để nhận biến `PORT` do Cloud inject vào.
6. **Bật chế độ Debug (`reload=True`) ở môi trường Production**: Gây hao tổn tài nguyên hệ thống, giảm hiệu năng và tiềm ẩn nguy cơ bảo mật do hiển thị stack trace chi tiết khi có lỗi.
7. **Không xử lý Graceful Shutdown**: Không bắt các tín hiệu `SIGTERM` / `SIGINT`, dẫn đến việc tiến trình bị ngắt đột ngột và làm gián đoạn các request đang xử lý dở dang (in-flight requests).
8. **Thiếu kiểm soát/Validate Input**: Endpoint `/ask` nhận raw string qua query parameters mà không có schema validation (như Pydantic models) để lọc input độc hại hoặc sai định dạng.

### Exercise 1.3: Comparison table
Dưới đây là bảng so sánh sự khác biệt giữa hai phiên bản Basic (localhost) và Advanced (production):

| Feature | Develop (Basic) | Production (Advanced) | Tại sao quan trọng? |
| :--- | :--- | :--- | :--- |
| **Config** | Hardcode trực tiếp các cấu hình và secrets trong mã nguồn. | Tải tập trung từ Environment Variables thông qua Pydantic Settings, có validation lúc khởi động. | Tuân thủ nguyên tắc 12-Factor App, giúp chạy cùng một source code trên nhiều môi trường khác nhau mà không cần sửa code, giữ bảo mật cho các API keys/secrets. |
| **Health check** | Không có endpoint kiểm tra sức khỏe của ứng dụng. | Cung cấp hai endpoint `/health` (Liveness) và `/ready` (Readiness). | Giúp các orchestrators hoặc Load Balancer phát hiện container bị crash để restart tự động, hoặc chỉ chuyển traffic vào container khi đã sẵn sàng (đã kết nối DB/Redis). |
| **Logging** | Dùng `print()` không có cấu trúc chuẩn, dễ in lộ secrets. | Sử dụng Structured JSON Logging ghi rõ log level, timestamp, event, và không in secrets. | Dễ dàng parse và lọc log trên các hệ thống giám sát tập trung (Loki, Datadog), giúp phát hiện lỗi nhanh và không bị rò rỉ thông tin nhạy cảm. |
| **Shutdown** | Đột ngột ngắt tiến trình ngay khi nhận tín hiệu tắt máy. | Graceful shutdown: bắt tín hiệu `SIGTERM` để dừng nhận request mới và hoàn thành nốt request cũ. | Tránh làm lỗi các giao dịch hoặc request của người dùng khi ứng dụng deploy phiên bản mới hoặc thực hiện scale-down. |

---

## Part 2: Docker

### Exercise 2.1: Dockerfile questions
1. **Base image**: `python:3.11` (Bản phân phối Python đầy đủ, kích thước lớn khoảng ~1GB).
2. **Working directory**: `/app` (Thư mục làm việc mặc định bên trong container).
3. **Tại sao COPY requirements.txt trước?**: Để tận dụng cơ chế Docker layer caching. Docker chỉ chạy lại bước cài dependencies (`pip install`) khi file `requirements.txt` thay đổi. Nếu chỉ sửa source code, Docker sẽ bỏ qua bước này giúp build image cực kỳ nhanh.
4. **CMD vs ENTRYPOINT**:
   - `ENTRYPOINT` định nghĩa câu lệnh chính thức sẽ chạy khi container khởi động và khó bị ghi đè (các tham số truyền vào lệnh `docker run` sẽ được append vào sau `ENTRYPOINT`).
   - `CMD` định nghĩa câu lệnh mặc định hoặc tham số mặc định truyền cho `ENTRYPOINT`. `CMD` có thể bị ghi đè một cách dễ dàng bằng cách truyền câu lệnh khác trực tiếp khi chạy `docker run`.

### Exercise 2.3: Image size comparison
- **Develop Image**: ~1.01 GB (Dùng base image `python:3.11` đầy đủ và giữ lại toàn bộ build tools, package caches).
- **Production Image**: ~150 MB (Dùng multi-stage build với base image `python:3.11-slim` ở stage runtime, chỉ copy các package đã cài đặt ở stage builder mà không mang theo compile tools).
- **Chênh lệch**: Giảm khoảng ~85% kích thước image.

### Exercise 2.4: Docker Compose stack
- **Các services được khởi động**:
  - `agent`: Service chạy ứng dụng FastAPI. Được build trực tiếp từ `Dockerfile` hiện tại, expose port `8000:8000`, có cấu hình check health và phụ thuộc vào service `redis`.
  - `redis`: Service cơ sở dữ liệu lưu trữ session và rate limit. Dùng image nhẹ `redis:7-alpine`, giới hạn memory tối đa `128mb` với policy `allkeys-lru` để tự giải phóng key cũ.
- **Cách thức giao tiếp**:
  - Hai services giao tiếp với nhau qua mạng ảo nội bộ do Docker Compose tự động tạo ra. Service `agent` kết nối tới Redis bằng hostname là `redis` qua port `6379` thông qua biến môi trường `REDIS_URL=redis://redis:6379/0`.

---

## Part 3: Cloud Deployment

### Exercise 3.1: Railway deployment
- **Public URL**: `https://day12-production-agent.up.railway.app` (Đường dẫn ví dụ thực tế sau khi deploy thành công).
- **Screenshots**: Các screenshot được lưu trữ trong thư mục `screenshots/` của repository:
  - `screenshots/dashboard.png` (Giao diện quản lý Railway Dashboard hiển thị các service và biến môi trường).
  - `screenshots/running.png` (Trạng thái deploy thành công và log chạy service).
  - `screenshots/test.png` (Kết quả gọi API thành công).

### Exercise 3.2: Render deployment comparison
- **Sự khác biệt giữa `render.yaml` và `railway.toml`**:
  - `railway.toml` là file cấu hình riêng cho Railway để điều khiển quá trình build (ví dụ dùng Dockerfile) và deploy (startup command, health check). Nó khá đơn giản và tập trung vào deploy một service đơn lẻ.
  - `render.yaml` là file cấu hình Blueprint của Render. Nó mạnh mẽ hơn, cho phép mô tả cả một hệ thống gồm nhiều service khác nhau (Web Service, Background Worker, Database, Redis), định nghĩa region (như `singapore` để giảm latency), định nghĩa instance types (như `starter`), và hỗ trợ tự động sinh các giá trị ngẫu nhiên cho secrets (`generateValue: true`).

---

## Part 4: API Security

### Exercise 4.1-4.3: Test results
- **API Key Authentication (Exercise 4.1)**:
  - Gọi request không kèm API key hoặc sai key:
    ```json
    HTTP/1.1 401 Unauthorized
    {
      "detail": "Invalid or missing API key. Include header: X-API-Key: <key>"
    }
    ```
  - Gọi request kèm đúng header `X-API-Key: your-secret-key`:
    ```json
    HTTP/1.1 200 OK
    {
      "question": "Docker là gì?",
      "answer": "Container là cách đóng gói app để chạy ở mọi nơi...",
      "model": "gpt-4o-mini",
      "timestamp": "2026-06-12T10:00:00Z"
    }
    ```
- **JWT Authentication (Exercise 4.2)**:
  - Đăng nhập lấy Token thành công qua `/token`. Sử dụng token thu được để gọi `/ask` qua header `Authorization: Bearer <token>` trả về HTTP `200 OK`.
- **Rate Limiting (Exercise 4.3)**:
  - Khi gọi dồn dập vượt quá 10 req/phút:
    ```json
    HTTP/1.1 429 Too Many Requests
    Retry-After: 60
    {
      "detail": "Rate limit exceeded: 20 req/min"
    }
    ```

### Exercise 4.4: Cost guard implementation
- **Cách thức implement**:
  - Sử dụng Redis-backed Cost Guard kết hợp In-memory Fallback.
  - Mỗi khi nhận request, ước lượng số token đầu vào/đầu ra dựa trên độ dài chuỗi ký tự.
  - Trước khi gọi LLM, kiểm tra tổng tiền đã tiêu dùng trong ngày (key `cost_guard:YYYY-MM-DD` trong Redis).
  - If số tiền tích lũy vượt quá Daily Budget ($5.0 USD), chặn cuộc gọi và trả về mã lỗi `402 Payment Required`.
  - Nếu hợp lệ, cho phép gọi LLM và cập nhật cộng dồn số tiền tiêu dùng vào Redis bằng lệnh `incrbyfloat` với thời gian hết hạn (TTL) là 24 giờ.

---

## Part 5: Scaling & Reliability

### Exercise 5.1-5.5: Implementation notes
- **5.1: Health checks**: 
  - Endpoint `/health` (Liveness) trả về `200 OK` khi ứng dụng đang chạy bình thường.
  - Endpoint `/ready` (Readiness) thực hiện ping tới Redis. Nếu kết nối Redis bị gián đoạn, trả về mã lỗi `503 Service Unavailable` để load balancer tạm thời không chuyển request đến instance này.
- **5.2: Graceful shutdown**: 
  - Đăng ký hàm handle signal `SIGTERM` gửi từ orchestrator (Railway/Docker). Khi nhận tín hiệu tắt, ứng dụng chuyển trạng thái ready sang `False`, uvicorn dừng nhận request mới và chờ 30 giây (`timeout_graceful_shutdown=30`) để các request đang xử lý hoàn thành trước khi dừng hẳn.
- **5.3: Stateless design**: 
  - Toàn bộ session lưu trữ hội thoại, bộ đếm rate limit và thông tin budget cost guard đều được đẩy vào Redis dùng chung thay vì lưu ở memory local của container.
- **5.4: Load balancing**: 
  - Triển khai Nginx làm load balancer phía trước 3 instance của agent. Các request từ cùng một user có thể được chuyển tới các instance khác nhau một cách luân phiên mà không bị mất dữ liệu hội thoại hay vượt rate limit.
- **5.5: Stateless test results**: 
  - Khi chạy script `test_stateless.py`, chúng ta kill ngẫu nhiên một instance của agent giữa chừng. Request tiếp theo được route qua instance khác và vẫn tiếp tục cuộc hội thoại bình thường mà không bị mất context. Điều này chứng minh thiết kế stateless hoạt động hoàn hảo.
