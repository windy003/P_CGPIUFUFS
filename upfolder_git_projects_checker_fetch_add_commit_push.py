#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
检测父文件夹下的所有git项目，并统计每个项目的untracked files
"""

import os
import subprocess
import concurrent.futures
import threading
from pathlib import Path
from urllib.parse import quote


def load_env(env_path):
    """从 .env 文件加载环境变量"""
    if not os.path.isfile(env_path):
        return {}
    env_vars = {}
    with open(env_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                key, value = line.split('=', 1)
                env_vars[key.strip()] = value.strip()
    return env_vars


def make_clickable_path(path):
    """将Windows路径转换为可点击的超链接格式（OSC 8标准）"""
    # 将反斜杠转换为正斜杠
    normalized_path = path.replace('\\', '/')
    # 对路径进行URL编码
    encoded_path = quote(normalized_path, safe='/:')
    # 创建file:// URL
    file_url = f"file:///{encoded_path}"
    # 使用OSC 8格式创建超链接
    # 格式: \033]8;;URL\033\\显示文本\033]8;;\033\\
    hyperlink = f"\033]8;;{file_url}\033\\{path}\033]8;;\033\\"
    return hyperlink


def is_git_repo(folder_path):
    """检查文件夹是否是git仓库"""
    git_dir = os.path.join(folder_path, '.git')
    return os.path.isdir(git_dir)


def do_git_fetch(repo_path):
    """执行 git fetch origin 命令"""
    try:
        result = subprocess.run(
            ['git', 'fetch', 'origin'],
            cwd=repo_path,
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=30  # 30秒超时
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return False


def get_remote_sync_status(repo_path):
    """获取与远程仓库的同步状态"""
    try:
        # 检查是否有 origin 远程
        result = subprocess.run(
            ['git', 'remote', '-v'],
            cwd=repo_path,
            capture_output=True,
            text=True,
            encoding='utf-8'
        )

        if result.returncode != 0 or 'origin' not in result.stdout:
            return {'has_remote': False, 'message': '没有配置 origin 远程'}

        # 获取当前分支名
        result = subprocess.run(
            ['git', 'branch', '--show-current'],
            cwd=repo_path,
            capture_output=True,
            text=True,
            encoding='utf-8'
        )

        if result.returncode != 0 or not result.stdout.strip():
            return {'has_remote': True, 'message': '无法获取当前分支（可能处于分离HEAD状态）'}

        current_branch = result.stdout.strip()

        # 检查是否有上游分支
        result = subprocess.run(
            ['git', 'rev-parse', '--abbrev-ref', '@{upstream}'],
            cwd=repo_path,
            capture_output=True,
            text=True,
            encoding='utf-8'
        )

        if result.returncode != 0:
            # 尝试检查 origin 上是否存在同名分支
            result = subprocess.run(
                ['git', 'rev-parse', '--verify', f'origin/{current_branch}'],
                cwd=repo_path,
                capture_output=True,
                text=True,
                encoding='utf-8'
            )
            if result.returncode != 0:
                return {
                    'has_remote': True,
                    'branch': current_branch,
                    'message': f'分支 {current_branch} 未设置上游追踪，且 origin/{current_branch} 不存在'
                }
            upstream_branch = f'origin/{current_branch}'
        else:
            upstream_branch = result.stdout.strip()

        # 获取本地领先远程的提交数
        result = subprocess.run(
            ['git', 'rev-list', '--count', f'{upstream_branch}..HEAD'],
            cwd=repo_path,
            capture_output=True,
            text=True,
            encoding='utf-8'
        )
        ahead = int(result.stdout.strip()) if result.returncode == 0 else 0

        # 获取远程领先本地的提交数
        result = subprocess.run(
            ['git', 'rev-list', '--count', f'HEAD..{upstream_branch}'],
            cwd=repo_path,
            capture_output=True,
            text=True,
            encoding='utf-8'
        )
        behind = int(result.stdout.strip()) if result.returncode == 0 else 0

        # 构建状态信息
        sync_status = {
            'has_remote': True,
            'branch': current_branch,
            'upstream': upstream_branch,
            'ahead': ahead,
            'behind': behind
        }

        if ahead == 0 and behind == 0:
            sync_status['status'] = 'synced'
            sync_status['message'] = '已同步'
        elif ahead > 0 and behind == 0:
            sync_status['status'] = 'ahead'
            sync_status['message'] = f'领先远程 {ahead} 个提交'
        elif ahead == 0 and behind > 0:
            sync_status['status'] = 'behind'
            sync_status['message'] = f'落后远程 {behind} 个提交'
        else:
            sync_status['status'] = 'diverged'
            sync_status['message'] = f'已分叉：本地领先 {ahead} 个提交，落后 {behind} 个提交'

        return sync_status

    except Exception as e:
        return {'has_remote': False, 'message': f'检查远程状态时出错: {e}'}


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


def do_git_add_commit_push(repo_path, commit_message):
    """对指定仓库执行 git add . → git commit → git push"""
    repo_name = os.path.basename(repo_path)
    results = {'add': False, 'commit': False, 'push': False, 'errors': []}

    # git add .
    try:
        result = subprocess.run(
            ['git', 'add', '.'],
            cwd=repo_path,
            capture_output=True,
            text=True,
            encoding='utf-8'
        )
        if result.returncode == 0:
            results['add'] = True
        else:
            results['errors'].append(f"git add 失败: {result.stderr.strip()}")
            return results
    except Exception as e:
        results['errors'].append(f"git add 异常: {e}")
        return results

    # git commit -m "message"
    try:
        result = subprocess.run(
            ['git', 'commit', '-m', commit_message],
            cwd=repo_path,
            capture_output=True,
            text=True,
            encoding='utf-8'
        )
        if result.returncode == 0:
            results['commit'] = True
        else:
            stderr = result.stderr.strip()
            stdout = result.stdout.strip()
            # nothing to commit 不算错误
            if 'nothing to commit' in stdout:
                results['errors'].append("没有需要提交的变更")
            else:
                results['errors'].append(f"git commit 失败: {stderr or stdout}")
            return results
    except Exception as e:
        results['errors'].append(f"git commit 异常: {e}")
        return results

    # git push
    try:
        result = subprocess.run(
            ['git', 'push'],
            cwd=repo_path,
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=60
        )
        if result.returncode == 0:
            results['push'] = True
        else:
            results['errors'].append(f"git push 失败: {result.stderr.strip()}")
    except subprocess.TimeoutExpired:
        results['errors'].append("git push 超时（60秒）")
    except Exception as e:
        results['errors'].append(f"git push 异常: {e}")

    return results


def scan_directory_for_git_repos(directory):
    """递归扫描指定目录下的所有子目录，查找git仓库"""
    git_repos = []

    try:
        # 使用 os.walk 递归遍历所有子目录
        for root, dirs, files in os.walk(directory):
            # 检查当前目录是否是git仓库
            if is_git_repo(root):
                git_repos.append(root)
                # 如果当前目录是git仓库，不再深入其子目录
                # 因为git仓库内部的.git不算独立仓库
                dirs.clear()
                continue

            # 过滤掉一些不需要扫描的目录（可选优化）
            # 移除以.开头的隐藏目录（除了.git已经在上面处理）
            dirs[:] = [d for d in dirs if not d.startswith('.')]

    except PermissionError:
        # 跳过没有权限访问的目录
        pass
    except Exception as e:
        # 跳过其他错误
        pass

    return git_repos


def process_repo(repo_path, progress_counter, total, print_lock):
    """并行处理单个仓库：fetch、获取状态、获取同步状态"""
    repo_name = os.path.basename(repo_path)

    do_git_fetch(repo_path)
    status_info = get_git_status(repo_path)
    sync_status = get_remote_sync_status(repo_path)

    with print_lock:
        progress_counter[0] += 1
        print(f"\r正在处理 ({progress_counter[0]}/{total}): {repo_name}{'':30}", end='', flush=True)

    return {
        'name': repo_name,
        'path': repo_path,
        'status': status_info,
        'sync': sync_status,
    }


def push_repo(repo, commit_msg, print_lock):
    """并行执行单个仓库的 add/commit/push"""
    results = do_git_add_commit_push(repo['path'], commit_msg)
    with print_lock:
        print(f"  完成: {repo['name']} — push {'✓' if results['push'] else '✗'}")
    return repo, results


def main():
    # 从 .env 文件读取目标目录
    script_dir = Path(__file__).resolve().parent
    env_path = script_dir / '.env'
    env_vars = load_env(str(env_path))

    target_dir = env_vars.get('TARGET_DIR')
    if not target_dir:
        print("错误: .env 文件中未设置 TARGET_DIR")
        print(f"请在 {env_path} 中添加 TARGET_DIR=<要扫描的目录路径>")
        input("Press Enter to exit...")
        return
    parent_dir = Path(target_dir)
    if not parent_dir.is_dir():
        print(f"错误: TARGET_DIR 指定的目录不存在: {target_dir}")
        input("Press Enter to exit...")
        return

    print(f"正在递归扫描上层目录及其所有子目录: {make_clickable_path(str(parent_dir))}")
    print("=" * 80)
    print()

    repos_with_changes = []
    repos_with_sync_issues = []
    total_modified_count = 0
    total_staged_count = 0
    total_untracked_count = 0
    total_ahead = 0
    total_behind = 0

    print("正在扫描目录...")
    repos_in_dir = scan_directory_for_git_repos(parent_dir)
    total = len(repos_in_dir)
    print(f"发现 {total} 个 Git 仓库，开始并行获取状态...\n")

    # 并行处理所有仓库（fetch 是主要耗时，I/O 密集用线程池）
    max_workers = min(total, 16) if total > 0 else 1
    print_lock = threading.Lock()
    progress_counter = [0]
    repo_results = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(process_repo, rp, progress_counter, total, print_lock): rp
            for rp in repos_in_dir
        }
        for future in concurrent.futures.as_completed(futures):
            try:
                repo_results.append(future.result())
            except Exception as e:
                repo_path = futures[future]
                with print_lock:
                    print(f"\n  ⚠ 处理 {repo_path} 时出错: {e}")

    # 清除进度行
    print("\r" + " " * 70 + "\r", end='', flush=True)

    # 按原始顺序排列结果
    order = {rp: i for i, rp in enumerate(repos_in_dir)}
    repo_results.sort(key=lambda r: order.get(r['path'], 0))

    # 整理统计数据
    for r in repo_results:
        status_info = r['status']
        sync_status = r['sync']

        if status_info:
            repos_with_changes.append({'name': r['name'], 'path': r['path'], 'status': status_info})
            total_modified_count += len(status_info['modified'])
            total_staged_count += len(status_info['staged'])
            total_untracked_count += len(status_info['untracked'])

        if sync_status.get('has_remote'):
            status = sync_status.get('status')
            if status and status != 'synced':
                repos_with_sync_issues.append({'name': r['name'], 'path': r['path'], 'sync': sync_status})
                total_ahead += sync_status.get('ahead', 0)
                total_behind += sync_status.get('behind', 0)
            elif not status:
                repos_with_sync_issues.append({'name': r['name'], 'path': r['path'], 'sync': sync_status})

    print()
    print("=" * 80)

    # 输出结果
    print(f"找到 {total} 个 Git 仓库")
    print(f"其中 {len(repos_with_changes)} 个仓库有本地变更")
    print(f"其中 {len(repos_with_sync_issues)} 个仓库与远程不同步")
    print()

    if repos_with_changes:
        print("=" * 80)
        print("有变更的仓库详情:")
        print("=" * 80)
        print()

        for repo in repos_with_changes:
            print(f"📁 {repo['name']}")
            print(f"   路径: {make_clickable_path(repo['path'])}")

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
        print(f"本地变更总计:")
        print(f"  已暂存: {total_staged_count} 个文件")
        print(f"  已修改未暂存: {total_modified_count} 个文件")
        print(f"  未跟踪: {total_untracked_count} 个文件")
        print("=" * 80)
    else:
        print("✓ 所有Git仓库都是干净的状态（没有本地变更）")

    # 显示远程同步状态
    print()
    if repos_with_sync_issues:
        print("=" * 80)
        print("与远程不同步的仓库:")
        print("=" * 80)
        print()

        for repo in repos_with_sync_issues:
            sync = repo['sync']
            print(f"📁 {repo['name']}")
            print(f"   路径: {make_clickable_path(repo['path'])}")

            if sync.get('branch'):
                print(f"   分支: {sync['branch']}")
            if sync.get('upstream'):
                print(f"   上游: {sync['upstream']}")

            status = sync.get('status')
            message = sync.get('message', '')

            if status == 'ahead':
                print(f"   ⬆ {message}")
            elif status == 'behind':
                print(f"   ⬇ {message}")
            elif status == 'diverged':
                print(f"   ⚡ {message}")
            else:
                print(f"   ⚠ {message}")

            print()

        print("=" * 80)
        print(f"远程同步总计:")
        print(f"  本地领先远程: {total_ahead} 个提交")
        print(f"  本地落后远程: {total_behind} 个提交")
        print("=" * 80)
    else:
        print("✓ 所有有远程配置的Git仓库都已与远程同步")

    # 批量 add / commit / push
    if repos_with_changes:
        print()
        print("=" * 80)
        choice = input("是否要对以上所有有变更的仓库执行 git add . / git commit / git push？(y/n): ").strip().lower()

        if choice == 'y':
            commit_msg = input("请输入 commit 信息: ").strip()
            if not commit_msg:
                print("commit 信息不能为空，已取消操作。")
                return

            print()
            print(f"即将对 {len(repos_with_changes)} 个仓库执行操作，commit 信息: \"{commit_msg}\"")
            print("=" * 80)
            print()

            push_lock = threading.Lock()
            push_workers = min(len(repos_with_changes), 8)
            push_results = []  # list of (repo, results)

            with concurrent.futures.ThreadPoolExecutor(max_workers=push_workers) as executor:
                futures = {
                    executor.submit(push_repo, repo, commit_msg, push_lock): repo
                    for repo in repos_with_changes
                }
                for future in concurrent.futures.as_completed(futures):
                    try:
                        push_results.append(future.result())
                    except Exception as e:
                        repo = futures[future]
                        push_results.append((repo, {'add': False, 'commit': False, 'push': False, 'errors': [str(e)]}))

            print()

            # 按原始顺序打印详细结果
            repo_order = {repo['path']: i for i, repo in enumerate(repos_with_changes)}
            push_results.sort(key=lambda x: repo_order.get(x[0]['path'], 0))

            success_count = 0
            fail_count = 0

            for repo, results in push_results:
                print(f"📁 {repo['name']}")
                print(f"   路径: {repo['path']}")
                print(f"   git add .    : {'✓ 成功' if results['add'] else '✗ 失败'}")
                print(f"   git commit   : {'✓ 成功' if results['commit'] else '✗ 失败'}")
                print(f"   git push     : {'✓ 成功' if results['push'] else '✗ 失败'}")
                if results['errors']:
                    for err in results['errors']:
                        print(f"   ⚠ {err}")
                if results['push']:
                    success_count += 1
                else:
                    fail_count += 1
                print()

            print("=" * 80)
            print(f"批量操作完成: 成功 {success_count} 个，失败 {fail_count} 个")
            print("=" * 80)
        else:
            print("已取消操作。")


if __name__ == '__main__':
    main()
    input("Press Enter to continue...")
