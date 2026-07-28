# 权限管理策略

## 目标

平台面向数据维护员、分析师、管理员和后续 AI Agent 工作流。权限策略需要接近常见 ToC/中后台平台实践，同时不能过早复杂化。

第一阶段采用：

```text
RBAC + 菜单权限 + 操作权限 + 数据范围
```

## 角色模型

默认角色：

- `super_admin`: 超级管理员，拥有全部权限。
- `admin`: 管理员，管理用户、角色、元数据和数据。
- `data_maintainer`: 数据维护员，负责导入、录入、修正竞品数据。
- `analyst`: 分析师，查看数据、创建分析报告、使用 AI Agent。
- `viewer`: 只读用户，只能查看被授权的数据。

## 权限类型

### 菜单权限

控制用户能看到哪些模块：

- dashboard
- vehicles
- metadata
- structure_tree
- import_export
- visualization
- ai_agent
- permission

### 操作权限

控制按钮和 API 行为：

- create
- read
- update
- delete
- import
- export
- approve
- analyze

### 数据范围权限

控制用户可以访问哪些业务数据：

- `all`: 全部数据
- `department`: 所属组织数据
- `self`: 本人创建数据
- `custom`: 自定义范围

第一阶段可以先实现配置结构，后端接口预留校验入口。

## 权限编码规范

使用稳定权限编码：

```text
resource:action
```

示例：

```text
metadata:read
metadata:update
vehicle:create
vehicle:import
asset:export
agent:analyze
permission:manage
```

## 鉴权流程

```text
用户登录
  ↓
获取用户角色
  ↓
聚合角色权限
  ↓
前端按菜单/按钮权限渲染
  ↓
后端按 API 权限二次校验
  ↓
数据查询附加数据范围条件
```

## 为什么不用过重权限模型

第一阶段不做多租户、不做字段级权限、不做审批流。原因：

- MVP 的主目标是跑通数据平台能力。
- 字段级权限会显著增加动态表单和导入导出复杂度。
- 多租户可以后续通过 `tenant_id` 平滑加入。

## 后续扩展

后续可扩展：

- 多租户隔离：所有核心表增加 `tenant_id`。
- 字段级权限：对 `meta_attribute` 做读写控制。
- 数据审批流：数据维护员提交，管理员审核发布。
- Agent 权限：控制 AI Agent 可读取的数据范围和可调用工具。

