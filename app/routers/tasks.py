"""定时任务 API：创建、列出、删除任务。"""

from fastapi import APIRouter
from app.models import TaskCreate, ErrorResponse

import memory.store as store

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("", summary="列出所有任务")
async def list_tasks():
    return store.list_tasks()


@router.post("", summary="��建定时任务")
async def create_task(req: TaskCreate):
    task = store.create_task(req)
    return {"ok": True, "task": task}


@router.delete("/{task_id}", summary="删除任务")
async def delete_task(task_id: str):
    if store.delete_task(task_id):
        return {"ok": True, "message": f"已删除任务 {task_id}"}
    return ErrorResponse(error="任务不存在")
