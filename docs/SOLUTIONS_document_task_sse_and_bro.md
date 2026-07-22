# 📖 Architectural Design Documentation: Real-Time Task Orchestration via Server-Sent Events (SSE)

## Overview and Goals

This document details the architecture for managing long-running, asynchronous tasks within a modern web application stack. The primary goal is to provide real-time progress updates to the client (React Consumers) without relying on constant polling or WebSockets (when simple server-to-client communication suffices). We utilize Server-Sent Events (SSE) to establish a persistent, unidirectional connection from the backend service to the frontend UI component.

The architecture defines clear boundaries for state authority, ensuring that while React presents the mutable view, the dedicated API State Service remains the single source of truth regarding task status and progress.

---

## 🇬🇧 English Documentation

### 1. Workflow Description: Task Lifecycle via SSE

The process begins when a user initiates a long-running task (e.g., generating a complex report or performing extensive data transformation).

**Initiation & State Authority:**
1.  **Client Request:** The React component sends an initial `POST` request to the API Gateway (`/tasks/submit`). This payload includes all necessary input parameters.
2.  **API Gateway (Authority):** The gateway receives the task, validates inputs, and immediately records a preliminary status (`PENDING`) in the centralized Task State Service. It responds instantly with a unique `task_id` and confirmation of submission.
3.  **Task Processor:** A dedicated background worker or message queue service picks up the task ID. This processor executes the time-intensive business logic asynchronously, decoupled from the web request thread.

**Progress Tracking (SSE):**
1.  **Connection Establishment:** Once submitted, the React consumer initiates a second connection: an SSE stream (`GET /tasks/{task_id}/progress`).
2.  **Server Push:** The Task Processor updates its status and sends these incremental changes (e.g., "Step 1/5 Complete," "Processing Records X to Y") directly through the established SSE channel. Source 3 notes that SSE is ideal for this server-to-client scenario, optimizing connection stability over complex bidirectional protocols.
3.  **Client Consumption:** The React hook listens exclusively to this stream. As events arrive, it updates a local state machine, rendering continuous progress indicators without requiring manual re-fetches or timeouts.

### 2. Component Roles and Interactions

| Component | Role | Interaction Point | Details |
| :--- | :--- | :--- | :--- |
| **React Consumer (Client)** | Presentation Layer & State Listener | HTTP POST, SSE Connection (`/tasks/{id}/progress`) | Initiates tasks and listens for unidirectional updates. Responsible for rendering progress. |
| **API Gateway** | Entry Point & Validation | Request Routing | Handles authentication, rate limiting, and routes the initial request to the Task Queue endpoint. |
| **Task State Service (DB)** | Single Source of Truth (Authority) | Database Writes/Reads | Persists the task state, inputs, history, and final status (`COMPLETED`, `FAILED`). |
| **Task Processor (Worker)** | Execution Engine | Message Queue / Direct DB Update | Executes the heavy lifting. Pushes progress updates to the SSE connection associated with the `task_id`. |

### 3. Architectural Diagram: Task Flow State Machine

The diagram illustrates the communication flow, emphasizing the separation between the initial command request and the continuous status stream.

```mermaid
graph LR
    A[React Consumer] -->|1. POST /tasks/submit (Task Data)| B(API Gateway);
    B --> C{Task State Service};
    C -->|Record Task ID & PENDING Status| B;
    B -->|2. 202 Accepted + task_id| A;

    A -->|3. GET /tasks/{task_id}/progress (SSE Connection)| D(API Gateway);
    D --> E[Task Processor / Worker];
    E -->|4. Update Progress: {"status": "...", "step": N}| C;
    C -->|5. Push Event Stream (sse://)*| A;

    style A fill:#cceeff,stroke:#336699,stroke-width:2px
    style E fill:#ffccaa,stroke:#cc8800,stroke-width:2px
```

***

## 🇨🇳 中文文档 (Chinese Documentation)

### 1. 工作流描述：基于 SSE 的任务生命周期管理

本架构旨在解决在 Web 应用中处理长时间运行、异步任务的挑战。我们采用服务器发送事件（SSE）来建立一个持久、单向的连接，将后端服务推送到前端 UI 组件。这确保了即使任务耗时较长，客户端也能接收到实时的进度更新而不会发生超时。

**发起与状态权威：**
1.  **客户端请求 (Client Request):** 当用户发起一项长时间运行的任务（如生成大型报告）时，React 组件首先向 API 网关发送一个初始的 `POST` 请求 (`/tasks/submit`)。该载荷包含所有必需的输入参数。
2.  **API 网关 (Gateway) 与状态权威:** 网关接收请求，进行数据校验，并立即将初步状态（`待处理: PENDING`）记录到中央任务状态服务中。网关会立刻响应一个唯一的 `task_id` 和提交确认。
3.  **任务处理器 (Task Processor):** 一个独立的后台工作进程或消息队列消费者负责获取该任务 ID，并执行耗时业务逻辑。这一过程是异步的，与 Web 请求线程解耦。

**进度跟踪（SSE）：**
1.  **连接建立:** 任务提交后，React 组件会发起第二个请求：一个 SSE 流连接 (`GET /tasks/{task_id}/progress`)。
2.  **服务器推送 (Server Push):** 随着任务的执行，任务处理器不断更新其状态并使用已建立的 SSE 通道将这些增量变化（例如：“步骤 1/5 完成”、“处理记录 X 到 Y”）直接推送到前端。SSE 非常适合这种只要求单向通信的场景。
3.  **客户端消费 (Client Consumption):** React 挂钩器专门监听此流。每当事件到达，它就会更新本地状态机，从而渲染出连续的进度指示器，无需进行手动重新拉取或等待超时。

### 2. 组件角色与交互（Component Roles and Interactions）

| 组件 | 职责 (Role) | 交互点 (Interaction Point) | 详细说明 (Details) |
| :--- | :--- | :--- | :--- |
| **React Consumer (客户端)** | 展示层和状态监听器 | HTTP POST, SSE 连接 (`/tasks/{id}/progress`) | 发起任务，并监听单向更新。负责渲染进度条。 |
| **API 网关** | 入口点与校验 | 请求路由 (Request Routing) | 处理认证、限流，并将初始请求转发给任务队列端点。 |
| **任务状态服务 (数据库)** | 唯一事实来源（权威） | 数据库写入/读取 | 持久化记录任务状态、输入历史和最终结果（`已完成`, `失败`）。 |
| **任务处理器 (Worker)** | 执行引擎 | 消息队列 / 数据库更新 | 执行耗时计算。通过与 `task_id` 关联的 SSE 连接，推送进度更新。 |

### 3. 架构图：任务流状态机（Architectural Diagram: Task Flow State Machine）

下图描绘了通信流程，强调了初始命令请求和连续状态流之间的分离。

```mermaid
graph LR
    A[React Consumer (客户端)] -->|1. POST /tasks/submit (任务数据)| B(API 网关);
    B --> C{任务状态服务};
    C -->|记录 Task ID & PENDING 状态| B;
    B -->|2. 202 Accepted + task_id| A;

    A -->|3. GET /tasks/{task_id}/progress (SSE 连接)| D(API 网关);
    D --> E[任务处理器 / Worker];
    E -->|4. 更新进度: {"status": "...", "step": N}| C;
    C -->|5. 推送事件流 (sse://)*| A;

    style A fill:#cceeff,stroke:#336699,stroke-width:2px
    style E fill:#ffccaa,stroke:#cc8800,stroke-width:2px
```