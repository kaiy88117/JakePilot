# utils/ai/technician_service.py

from typing import List, Dict, Any
from db.db_router import DatabaseRouter
import logging

logger = logging.getLogger(__name__)

class TechnicianService:
    """技师服务类 - 管理技师数据和默认初始化"""
    
    def __init__(self):
        self.db = DatabaseRouter()
        self.legacy_strengths_by_name = {
            "张伟": "擅长深层组织按摩，力气大，善于缓解肩颈腰背酸痛，注重肌肉深层放松",
            "王强": "深层组织按摩专家，手法扎实，专注于运动损伤修复和肌肉放松",
            "李娜": "手法细腻，擅长舒缓放松，适合压力大、睡眠差人群",
            "赵敏": "精通经络推拿，善于调理亚健康，力气适中",
            "刘洋": "泰式按摩高手，拉伸到位，适合喜欢全身放松的客户",
            "孙丽": "芳香精油按摩，舒缓情绪，适合女性客户",
            "周杰": "中医推拿，针对颈椎、腰椎问题有丰富经验",
            "吴婷": "头部按摩和足疗专家，助眠效果好",
            "郑斌": "力气大，适合喜欢重手法的客户，善于肌肉放松",
            "何静": "淋巴引流、面部护理，适合美容养生需求",
        }
        
        # 默认技师数据（10人，其中有两位擅长内容接近）
        self.default_technicians = [
            {
                "name": "张伟",
                "gender": "男",
                "strength": "擅长空调安装、调试与基础故障检测"
            },
            {
                "name": "王强",
                "gender": "男",
                "strength": "擅长冰箱、洗衣机等大家电检测与维修"
            },
            {
                "name": "李娜",
                "gender": "女", 
                "strength": "擅长厨卫电器安装、调试与使用指导"
            },
            {
                "name": "赵敏",
                "gender": "女",
                "strength": "擅长智能家居联网、配置与故障排查"
            },
            {
                "name": "刘洋",
                "gender": "男",
                "strength": "擅长电视挂装、影音设备安装与调试"
            },
            {
                "name": "孙丽",
                "gender": "女",
                "strength": "擅长小家电检测、换新判定与使用指导"
            },
            {
                "name": "周杰",
                "gender": "男",
                "strength": "擅长空调制冷系统检测与常见故障维修"
            },
            {
                "name": "吴婷",
                "gender": "女",
                "strength": "擅长洗衣机、烘干机安装与排水故障处理"
            },
            {
                "name": "郑斌",
                "gender": "男",
                "strength": "擅长大型家电安装与复杂现场条件勘察"
            },
            {
                "name": "何静",
                "gender": "女",
                "strength": "擅长售后检测、故障复现与客户使用指导"
            }
        ]

    def initialize_default_technicians(self) -> bool:
        """初始化默认技师数据"""
        try:
            # 检查是否已有技师数据
            existing_technicians = self.db.technicians.get_all_technicians()
            
            if existing_technicians:
                defaults_by_name = {
                    item["name"]: item for item in self.default_technicians
                }
                migrated = 0
                for technician in existing_technicians:
                    name = technician.get("name")
                    if (
                        name in self.legacy_strengths_by_name
                        and technician.get("strength")
                        == self.legacy_strengths_by_name[name]
                    ):
                        self.db.technicians.update_technician(
                            technician["id"],
                            strength=defaults_by_name[name]["strength"],
                        )
                        migrated += 1
                if migrated:
                    logger.info("已迁移 %s 条项目内置工程师档案", migrated)
                logger.info(f"数据库中已有 {len(existing_technicians)} 位技师，跳过初始化")
                return True
            
            logger.info("数据库中无技师数据，开始初始化默认技师")
            
            # 添加默认技师
            for tech_data in self.default_technicians:
                try:
                    tech_id = self.db.technicians.add_technician(
                        name=tech_data['name'],
                        gender=tech_data['gender'],
                        strength=tech_data['strength']
                    )
                    logger.debug(f"添加技师: {tech_data['name']} (ID: {tech_id})")
                    
                except Exception as e:
                    logger.error(f"添加技师 {tech_data['name']} 失败: {e}")
                    return False
            
            # 验证初始化结果
            final_count = len(self.db.technicians.get_all_technicians())
            logger.info(f"技师初始化完成，共添加 {final_count} 位技师")
            return True
            
        except Exception as e:
            logger.error(f"技师初始化失败: {e}")
            return False

    def get_all_technicians(self) -> List[Dict[str, Any]]:
        """获取所有技师信息"""
        return self.db.technicians.get_all_technicians()

    def get_technician_by_name(self, name: str) -> Dict[str, Any]:
        """根据姓名获取技师信息"""
        return self.db.technicians.get_technician_by_name(name)

    def get_technician_by_id(self, technician_id: int) -> Dict[str, Any]:
        """根据ID获取技师信息"""
        return self.db.technicians.get_technician_by_id(technician_id)

    def get_technician_schedules(self, technician_id: int, date) -> List[Dict[str, Any]]:
        """获取技师指定日期的排班信息"""
        return self.db.technicians.get_technician_schedules(technician_id, date)

    def is_technician_available(self, technician_id: int, start_time, end_time) -> bool:
        """检查技师在指定时间段是否可用"""
        return self.db.technicians.is_technician_available(technician_id, start_time, end_time)

    def add_technician(self, name: str, gender: str = None, strength: str = None) -> int:
        """添加新技师"""
        return self.db.technicians.add_technician(name, gender, strength)

    def get_technicians_count(self) -> int:
        """获取技师总数"""
        technicians = self.db.technicians.get_all_technicians()
        return len(technicians)

    def get_technician_by_id(self, technician_id: int) -> Dict[str, Any]:
        """根据ID获取技师信息"""
        return self.db.technicians.get_technician_by_id(technician_id)

    def get_technician_schedules(self, technician_id: int, date) -> List[Dict[str, Any]]:
        """获取技师指定日期的排班信息"""
        return self.db.technicians.get_technician_schedules(technician_id, date)

    def is_technician_available(self, technician_id: int, start_time, end_time) -> bool:
        """检查技师在指定时间段是否可用"""
        return self.db.technicians.is_technician_available(technician_id, start_time, end_time)
