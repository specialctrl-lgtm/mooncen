# utils package
# 기존 utils.py의 함수들을 re-export
import sys
import os

# utils.py 파일에서 함수들 import
current_dir = os.path.dirname(__file__)
parent_dir = os.path.dirname(current_dir)
sys.path.insert(0, parent_dir)

# utils.py에서 필요한 함수들 가져오기
import importlib.util
spec = importlib.util.spec_from_file_location("utils_module", os.path.join(parent_dir, "utils.py"))
utils_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(utils_module)

# 함수들을 현재 패키지에서 사용 가능하도록 export
setup_logger = utils_module.setup_logger
parse_date = utils_module.parse_date
extract_number = utils_module.extract_number
extract_krw_amount = utils_module.extract_krw_amount
extract_material_fee_amount = utils_module.extract_material_fee_amount
infer_course_status = utils_module.infer_course_status
clean_text = utils_module.clean_text
clean_instructor_name = utils_module.clean_instructor_name
ensure_dir = utils_module.ensure_dir

__all__ = ['setup_logger', 'parse_date', 'extract_number', 'extract_krw_amount', 'extract_material_fee_amount', 'infer_course_status', 'clean_text', 'clean_instructor_name', 'ensure_dir']
