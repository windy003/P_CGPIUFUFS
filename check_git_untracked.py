#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
检测父文件夹下的所有git项目，并统计每个项目的untracked files
"""

import os
import subprocess
from pathlib import Path


def is_git_repo(folder_path):
    """检查文件夹是否是git仓库"""
    git_dir = os.path.join(folder_path, '.git')
    return os.path.isdir(git_dir)


def get_git_status(repo_path):
    """获取git仓库的状态信息"""
    try:
        # 运行 git status 命令
        result = subprocess.run(
            ['git', 'status'],
            cwd=repo_path,
            capture_output=True,
            text=True,
            encoding='utf-8'
        )

        if result.returncode != 0:
            return None

        output = result.stdout
        status_info = {
            'modified': [],      # 已修改但未暂存
            'staged': [],        # 已暂存
            'untracked': []      # 未跟踪
        }

        # 解析输出
        lines = output.split('\n')
        current_section = None

        for line in lines:
            # 检测不同的section
            if 'Changes to be committed:' in line:
                current_section = 'staged'
                continue
            elif 'Changes not staged for commit:' in line:
                current_section = 'modified'
                continue
            elif 'Untracked files:' in line:
                current_section = 'untracked'
                continue

            # 检测section结束
            if current_section:
                # 空行或非缩进行（不是提示信息）结束当前section
                if line.strip() == '':
                    # 遇到空行，可能结束section，但继续检查
                    pass
                elif not line.startswith('\t') and not line.startswith('  '):
                    # 检查是否是提示信息
                    if ('use "git' in line.lower() or
                        'include in what will be committed' in line.lower() or
                        'no changes added' in line.lower()):
                        continue
                    else:
                        # 非缩进的非提示行，结束section
                        current_section = None
                        continue

                # 提取文件名
                stripped = line.strip()
                if stripped and not stripped.startswith('('):
                    # 对于 modified 和 staged，需要去掉状态前缀（如 "modified:"）
                    if current_section in ['modified', 'staged']:
                        # 处理类似 "modified:   file.txt" 的格式
                        if ':' in stripped:
                            parts = stripped.split(':', 1)
                            if len(parts) == 2:
                                file_name = parts[1].strip()
                                status_prefix = parts[0].strip()
                                status_info[current_section].append(f"{status_prefix}: {file_name}")
                        else:
                            status_info[current_section].append(stripped)
                    elif current_section == 'untracked':
                        status_info[current_section].append(stripped)

        # 只返回有内容的状态信息
        if status_info['modified'] or status_info['staged'] or status_info['untracked']:
            return status_info
        else:
            return None

    except Exception as e:
        print(f"错误: 无法检查 {repo_path}: {e}")
        return None


def main():
    # 获取脚本所在目录的父目录
    script_dir = Path(__file__).resolve().parent
    parent_dir = script_dir.parent

    print(f"扫描目录: {parent_dir}")
    print("=" * 80)
    print()

    git_repos = []
    repos_with_changes = []
    total_modified_count = 0
    total_staged_count = 0
    total_untracked_count = 0

    # 遍历父目录下的所有子文件夹
    try:
        for item in os.listdir(parent_dir):
            item_path = os.path.join(parent_dir, item)

            # 只检查文件夹
            if not os.path.isdir(item_path):
                continue

            # 检查是否是git仓库
            if is_git_repo(item_path):
                git_repos.append(item)

                # 获取git状态
                status_info = get_git_status(item_path)

                if status_info:
                    repos_with_changes.append({
                        'name': item,
                        'path': item_path,
                        'status': status_info
                    })
                    total_modified_count += len(status_info['modified'])
                    total_staged_count += len(status_info['staged'])
                    total_untracked_count += len(status_info['untracked'])

    except Exception as e:
        print(f"错误: {e}")
        return

    # 输出结果
    print(f"找到 {len(git_repos)} 个 Git 仓库")
    print(f"其中 {len(repos_with_changes)} 个仓库有变更")
    print()

    if repos_with_changes:
        print("=" * 80)
        print("有变更的仓库详情:")
        print("=" * 80)
        print()

        for repo in repos_with_changes:
            print(f"📁 {repo['name']}")
            print(f"   路径: {repo['path']}")

            status = repo['status']

            # 显示已暂存的文件
            if status['staged']:
                print(f"   ✓ 已暂存 (Changes to be committed): {len(status['staged'])} 个文件")
                for file in status['staged']:
                    print(f"      - {file}")

            # 显示已修改但未暂存的文件
            if status['modified']:
                print(f"   ⚠ 已修改未暂存 (Changes not staged for commit): {len(status['modified'])} 个文件")
                for file in status['modified']:
                    print(f"      - {file}")

            # 显示未跟踪的文件
            if status['untracked']:
                print(f"   ? 未跟踪 (Untracked files): {len(status['untracked'])} 个文件")
                for file in status['untracked']:
                    print(f"      - {file}")

            print()

        print("=" * 80)
        print(f"总计:")
        print(f"  已暂存: {total_staged_count} 个文件")
        print(f"  已修改未暂存: {total_modified_count} 个文件")
        print(f"  未跟踪: {total_untracked_count} 个文件")
        print("=" * 80)
    else:
        print("✓ 所有Git仓库都是干净的状态（没有变更）")


if __name__ == '__main__':
    main()
    input("Press Enter to continue...")
