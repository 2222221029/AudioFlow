import requests
import json
from pathlib import Path

class BookshelfGetter:
    def __init__(self):
        self.session = requests.Session()
        self.user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0'
        
        self._setup_session()
    
    def _setup_session(self):
        """加载Cookie"""
        headers = {
            'User-Agent': self.user_agent,
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9',
        }
        self.session.headers.update(headers)
        
        try:
            if Path('cookies.json').exists():
                with open('cookies.json', 'r', encoding='utf-8') as f:
                    cookies_dict = json.load(f)
                    for name, value in cookies_dict.items():
                        self.session.cookies.set(name, value)
                print("[+] 已加载Cookie\n")
        except Exception as e:
            print(f"[-] 加载失败: {e}\n")
    
    def get_account_info(self):
        """获取账户信息"""
        print("="*70)
        print("📱 获取账户信息")
        print("="*70 + "\n")
        
        url = "https://wxapp.qidian.com/api/bookShelf/account"
        
        try:
            response = self.session.get(url, timeout=10)
            data = response.json()
            
            if data.get('code') == 0:
                user = data['data']['user']
                account = data['data']['accountInfo']
                
                print("[+] 用户信息:")
                print(f"    昵称: {user['nickName']}")
                print(f"    用户ID: {user['userId']}")
                print(f"    头像: {user['avatar']}")
                
                print(f"\n[+] 账户余额:")
                print(f"    总余额: {account['balanceTotal']} 元")
                print(f"    起点币: {account['qdBalance']} (免费: {account['qdFreeBalance']}, 付费: {account['qdWorthBalance']})")
                print(f"    阅豆: {account['ydBalance']} (免费: {account['ydFreeBalance']}, 付费: {account['ydWorthBalance']})")
                
                return user, account
        except Exception as e:
            print(f"[-] 异常: {e}")
        
        return None, None
    
    def get_bookshelf_list(self, page=1, pageSize=20):
        """获取书架列表"""
        print(f"\n{'='*70}")
        print(f"📚 获取书架列表 (第{page}页，每页{pageSize}本)")
        print("="*70 + "\n")
        
        url = "https://wxapp.qidian.com/api/bookShelf/list"
        params = {
            'page': page,
            'pageSize': pageSize,
        }
        
        try:
            response = self.session.get(url, params=params, timeout=10)
            data = response.json()
            
            if data.get('code') == 0:
                books_info = data['data'].get('booksInfo', [])
                page_info = data['data'].get('pageInfo', {})
                
                print(f"[+] 书架统计:")
                print(f"    总书数: {page_info.get('totalCount', 0)}")
                print(f"    当前页: {page_info.get('pageIndex', 1)}")
                print(f"    总页数: {page_info.get('pageCount', 0)}")
                
                if books_info:
                    print(f"\n[+] 书籍列表 ({len(books_info)} 本):")
                    print("-" * 70)
                    
                    books = []
                    for idx, book in enumerate(books_info, 1):
                        book_id = book.get('bookId')
                        book_name = book.get('bookName', '未知')
                        author = book.get('authorName', '未知')
                        last_chapter = book.get('lastChapterName', '未知')
                        update_time = book.get('updateTime', '未知')
                        
                        books.append({
                            'bookId': book_id,
                            'bookName': book_name,
                            'authorName': author,
                            'lastChapterName': last_chapter,
                            'updateTime': update_time
                        })
                        
                        print(f"\n    {idx}. 《{book_name}》")
                        print(f"       作者: {author}")
                        print(f"       最新章节: {last_chapter}")
                        print(f"       更新时间: {update_time}")
                    
                    # 保存为JSON
                    with open('bookshelf.json', 'w', encoding='utf-8') as f:
                        json.dump({
                            'pageInfo': page_info,
                            'books': books
                        }, f, ensure_ascii=False, indent=2)
                    
                    print(f"\n[+] 书架信息已保存到 bookshelf.json")
                    
                    return books
                else:
                    print("\n[-] 书架为空")
                    return []
            else:
                print(f"[-] 请求失败: {data.get('msg', '未知错误')}")
        except Exception as e:
            print(f"[-] 异常: {e}")
        
        return []
    
    def get_all_books(self):
        """获取所有书籍（支持分页）"""
        all_books = []
        page = 1
        
        while True:
            books = self.get_bookshelf_list(page=page, pageSize=20)
            
            if not books:
                break
            
            all_books.extend(books)
            page += 1
            
            # 防止过多请求
            if page > 10:
                print("[!] 达到最大页数限制")
                break
        
        return all_books

def main():
    print("\n" + "="*70)
    print("起点小说 - 书架信息获取工具")
    print("="*70 + "\n")
    
    getter = BookshelfGetter()
    
    # 获取账户信息
    user, account = getter.get_account_info()
    
    if user:
        # 获取书架列表
        books = getter.get_bookshelf_list(page=1, pageSize=50)
        
        if books:
            print(f"\n{'='*70}")
            print("[+] ✅ 成功获取书架信息!")
            print("="*70)
            print(f"\n[+] 获取到 {len(books)} 本书籍")
            print("[+] 详细信息已保存到 bookshelf.json")
        else:
            print("\n[-] 书架为空或获取失败")
    else:
        print("\n[-] 获取账户信息失败")

if __name__ == '__main__':
    main()
