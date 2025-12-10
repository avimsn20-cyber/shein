import requests
from bs4 import BeautifulSoup
import sqlite3
import time
import logging
import json
from datetime import datetime
import os
import threading
import re
import asyncio

# Configuration
CONFIG = {
    'telegram_bot_token': '8576313965:AAFMB7dTk6jU8YraDzc2yi6Bt62R2skq-0c',
    'telegram_chat_id': '1366899854',
    'admin_user_ids': ['1366899854'],
    'api_url': 'https://www.sheinindia.in/c/sverse-5939-37961',
    'check_interval_seconds': 2,
    'min_stock_threshold': 1,
    'database_path': '/tmp/shein_monitor.db',
    'min_increase_threshold_men': 2,  # Changed to 2 as requested
    'min_increase_threshold_women': 50
}

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class SheinStockMonitor:
    def __init__(self, config):
        self.config = config
        self.monitoring = False
        self.monitor_thread = None
        self.telegram_running = False
        self.last_notified_stock = 0  # Track last notified stock level
        self.setup_database()
        print("🤖 Shein Monitor initialized")
    
    def setup_database(self):
        """Initialize SQLite database with users table"""
        self.conn = sqlite3.connect(self.config['database_path'], check_same_thread=False)
        cursor = self.conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS stock_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                total_stock INTEGER,
                men_count INTEGER DEFAULT 0,
                women_count INTEGER DEFAULT 0,
                stock_change INTEGER DEFAULT 0,
                notified BOOLEAN DEFAULT FALSE
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bot_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT UNIQUE,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                chat_id TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                joined_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_interaction TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Add table for tracking notifications to prevent duplicates
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS stock_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_level INTEGER,
                notification_type TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                notified_count INTEGER DEFAULT 0
            )
        ''')
        
        self.conn.commit()
        print("✅ Database setup completed")
    
    def add_user(self, user_id, username, first_name, last_name, chat_id):
        """Add or update a user in the database"""
        cursor = self.conn.cursor()
        try:
            cursor.execute('''
                INSERT OR REPLACE INTO bot_users 
                (user_id, username, first_name, last_name, chat_id, is_active, last_interaction)
                VALUES (?, ?, ?, ?, ?, TRUE, CURRENT_TIMESTAMP)
            ''', (str(user_id), username, first_name, last_name, str(chat_id)))
            self.conn.commit()
            print(f"✅ User added/updated: {user_id} ({username})")
            return True
        except Exception as e:
            print(f"❌ Error adding user: {e}")
            return False
    
    def get_all_active_users(self):
        """Get all active users who should receive notifications"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT user_id, username, first_name, chat_id FROM bot_users WHERE is_active = TRUE')
        users = cursor.fetchall()
        return users
    
    def get_user_count(self):
        """Get total number of active users"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM bot_users WHERE is_active = TRUE')
        return cursor.fetchone()[0]
    
    def is_admin(self, user_id):
        """Check if user is admin"""
        return str(user_id) in self.config['admin_user_ids']
    
    def extract_men_count(self, data):
        """Extract ONLY men count from the JSON data"""
        men_count = 0
        
        try:
            # Method 1: Direct key access
            if 'genderfilter-Men' in data:
                men_data = data.get('genderfilter-Men', {})
                men_count = men_data.get('count', 0)
                print(f"✅ Found men count in genderfilter-Men: {men_count}")
                return men_count
            
            # Method 2: Search in nested objects
            if isinstance(data, dict):
                for key, value in data.items():
                    if isinstance(value, dict):
                        # Check if this is the men filter object
                        if 'genderfilter-Men' in key or ('name' in value and value.get('name') == 'Men'):
                            men_count = value.get('count', 0)
                            if men_count > 0:
                                print(f"✅ Found men count in {key}: {men_count}")
                                return men_count
            
            # Method 3: Regex search in string representation
            data_str = json.dumps(data)
            men_pattern = r'"genderfilter-Men":\s*\{[^}]*"count":\s*(\d+)'
            men_match = re.search(men_pattern, data_str)
            if men_match:
                men_count = int(men_match.group(1))
                print(f"✅ Found men count via regex: {men_count}")
                return men_count
            
            # Method 4: Alternative regex pattern
            men_pattern2 = r'"name":"Men"[^}]*"count":\s*(\d+)'
            men_match2 = re.search(men_pattern2, data_str)
            if men_match2:
                men_count = int(men_match2.group(1))
                print(f"✅ Found men count via alternative regex: {men_count}")
                return men_count
                
        except Exception as e:
            print(f"⚠️ Error extracting men count: {e}")
        
        print(f"ℹ️ Men count not found, defaulting to 0")
        return 0
    
    def extract_women_count(self, data):
        """Extract women count from the JSON data (only for manual checks)"""
        women_count = 0
        
        try:
            # Method 1: Direct key access
            if 'genderfilter-Women' in data:
                women_data = data.get('genderfilter-Women', {})
                women_count = women_data.get('count', 0)
                print(f"✅ Found women count in genderfilter-Women: {women_count}")
                return women_count
            
            # Method 2: Search in nested objects
            if isinstance(data, dict):
                for key, value in data.items():
                    if isinstance(value, dict):
                        # Check if this is the women filter object
                        if 'genderfilter-Women' in key or ('name' in value and value.get('name') == 'Women'):
                            women_count = value.get('count', 0)
                            if women_count > 0:
                                print(f"✅ Found women count in {key}: {women_count}")
                                return women_count
            
            # Method 3: Regex search in string representation
            data_str = json.dumps(data)
            women_pattern = r'"genderfilter-Women":\s*\{[^}]*"count":\s*(\d+)'
            women_match = re.search(women_pattern, data_str)
            if women_match:
                women_count = int(women_match.group(1))
                print(f"✅ Found women count via regex: {women_count}")
                return women_count
            
            # Method 4: Alternative regex pattern
            women_pattern2 = r'"name":"Women"[^}]*"count":\s*(\d+)'
            women_match2 = re.search(women_pattern2, data_str)
            if women_match2:
                women_count = int(women_match2.group(1))
                print(f"✅ Found women count via alternative regex: {women_count}")
                return women_count
                
        except Exception as e:
            print(f"⚠️ Error extracting women count: {e}")
        
        print(f"ℹ️ Women count not found, defaulting to 0")
        return 0
    
    def get_shein_stock_count(self):
        """Get men's stock count from Shein API"""
        try:
            headers = {
                'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                'accept-language': 'en-US,en;q=0.9',
                'cache-control': 'no-cache',
                'pragma': 'no-cache',
                'priority': 'u=0, i',
                'sec-ch-ua': '"Google Chrome";v="141", "Not?A_Brand";v="8", "Chromium";v="141"',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': '"Windows"',
                'sec-fetch-dest': 'document',
                'sec-fetch-mode': 'navigate',
                'sec-fetch-site': 'same-origin',
                'sec-fetch-user': '?1',
                'upgrade-insecure-requests': '1',
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36'
            }
            
            response = requests.get(
                self.config['api_url'],
                headers=headers,
                timeout=15
            )
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            scripts = soup.find_all('script')
            for script in scripts:
                script_content = script.string
                if script_content and 'facets' in script_content and 'totalResults' in script_content:
                    try:
                        if 'window.goodsDetailData' in script_content:
                            json_str = script_content.split('window.goodsDetailData = ')[1].split(';')[0]
                            data = json.loads(json_str)
                            men_count = self.extract_men_count(data)
                            women_count = self.extract_women_count(data)
                            total_stock = men_count + women_count
                            print(f"✅ Found men count: {men_count}, Women count: {women_count}, Total: {total_stock}")
                            return total_stock, men_count, women_count
                    except (json.JSONDecodeError, IndexError, KeyError) as e:
                        print(f"⚠️ Error parsing script data: {e}")
                        continue
            
            # Fallback: Search in response text
            response_text = response.text
            men_count = self.extract_men_count_from_text(response_text)
            women_count = self.extract_women_count_from_text(response_text)
            total_stock = men_count + women_count
            
            print(f"✅ Found via text search - Men: {men_count}, Women: {women_count}, Total: {total_stock}")
            return total_stock, men_count, women_count
            
        except requests.RequestException as e:
            print(f"❌ Error making API request: {e}")
            return 0, 0, 0
        except Exception as e:
            print(f"❌ Unexpected error during API call: {e}")
            return 0, 0, 0
    
    def extract_men_count_from_text(self, response_text):
        """Extract men count from response text using regex"""
        men_count = 0
        
        try:
            men_pattern = r'"genderfilter-Men":\s*\{[^}]*"count":\s*(\d+)'
            men_match = re.search(men_pattern, response_text)
            if men_match:
                men_count = int(men_match.group(1))
                print(f"✅ Found men count via text regex: {men_count}")
                return men_count
            
            men_pattern2 = r'"name":"Men"[^}]*"count":\s*(\d+)'
            men_match2 = re.search(men_pattern2, response_text)
            if men_match2:
                men_count = int(men_match2.group(1))
                print(f"✅ Found men count via alternative text regex: {men_count}")
                return men_count
                
        except Exception as e:
            print(f"⚠️ Error extracting men count from text: {e}")
        
        return men_count
    
    def extract_women_count_from_text(self, response_text):
        """Extract women count from response text using regex (only for manual checks)"""
        women_count = 0
        
        try:
            women_pattern = r'"genderfilter-Women":\s*\{[^}]*"count":\s*(\d+)'
            women_match = re.search(women_pattern, response_text)
            if women_match:
                women_count = int(women_match.group(1))
                print(f"✅ Found women count via text regex: {women_count}")
                return women_count
            
            women_pattern2 = r'"name":"Women"[^}]*"count":\s*(\d+)'
            women_match2 = re.search(women_pattern2, response_text)
            if women_match2:
                women_count = int(women_match2.group(1))
                print(f"✅ Found women count via alternative text regex: {women_count}")
                return women_count
                
        except Exception as e:
            print(f"⚠️ Error extracting women count from text: {e}")
        
        return women_count
    
    def get_previous_stock(self):
        """Get the last recorded stock count from database"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT total_stock, men_count, women_count FROM stock_history ORDER BY timestamp DESC LIMIT 1')
        result = cursor.fetchone()
        if result:
            return result[0], result[1], result[2]
        return 0, 0, 0
    
    def save_current_stock(self, current_stock, men_count, women_count, change=0, notified=False):
        """Save current stock count to database"""
        cursor = self.conn.cursor()
        cursor.execute('INSERT INTO stock_history (total_stock, men_count, women_count, stock_change, notified) VALUES (?, ?, ?, ?, ?)', 
                      (current_stock, men_count, women_count, change, notified))
        self.conn.commit()
    
    def has_stock_been_notified(self, stock_level, notification_type="men_stock"):
        """Check if we've already notified for this specific stock level"""
        cursor = self.conn.cursor()
        cursor.execute(
            'SELECT id FROM stock_notifications WHERE stock_level = ? AND notification_type = ? AND timestamp > datetime("now", "-1 hour")',
            (stock_level, notification_type)
        )
        return cursor.fetchone() is not None
    
    def record_notification(self, stock_level, notification_type="men_stock"):
        """Record that we've sent a notification for this stock level"""
        cursor = self.conn.cursor()
        cursor.execute(
            'INSERT INTO stock_notifications (stock_level, notification_type) VALUES (?, ?)',
            (stock_level, notification_type)
        )
        self.conn.commit()
    
    async def send_telegram_message(self, message, chat_id=None):
        """Send message via Telegram to specific chat_id"""
        try:
            if chat_id is None:
                chat_id = self.config['telegram_chat_id']
            
            url = f"https://api.telegram.org/bot{self.config['telegram_bot_token']}/sendMessage"
            payload = {
                'chat_id': chat_id,
                'text': message,
                'parse_mode': 'HTML'
            }
            
            response = requests.post(url, data=payload, timeout=10)
            response.raise_for_status()
            return True
        except Exception as e:
            print(f"❌ Error sending Telegram message to {chat_id}: {e}")
            return False
    
    async def send_telegram_message_with_keyboard(self, message, chat_id, is_admin=False):
        """Send message with custom keyboard"""
        try:
            if is_admin:
                keyboard = {
                    'keyboard': [
                        ['/start_monitor', '/stop_monitor'],
                        ['/check_now', '/status'],
                        ['/admin', '/users']
                    ],
                    'resize_keyboard': True,
                    'one_time_keyboard': False
                }
            else:
                keyboard = {
                    'keyboard': [
                        ['/check_now', '/status']
                    ],
                    'resize_keyboard': True,
                    'one_time_keyboard': False
                }
            
            url = f"https://api.telegram.org/bot{self.config['telegram_bot_token']}/sendMessage"
            payload = {
                'chat_id': chat_id,
                'text': message,
                'parse_mode': 'HTML',
                'reply_markup': json.dumps(keyboard)
            }
            
            response = requests.post(url, data=payload, timeout=10)
            response.raise_for_status()
            return True
        except Exception as e:
            print(f"❌ Error sending Telegram message with keyboard: {e}")
            return False
    
    async def broadcast_message(self, message):
        """Send message to ALL active users"""
        users = self.get_all_active_users()
        success_count = 0
        total_users = len(users)
        
        print(f"📢 Broadcasting message to {total_users} users...")
        
        for user in users:
            user_id, username, first_name, chat_id = user
            try:
                success = await self.send_telegram_message(message, chat_id)
                if success:
                    success_count += 1
                await asyncio.sleep(0.1)
            except Exception as e:
                print(f"❌ Error broadcasting to user {user_id}: {e}")
        
        print(f"✅ Broadcast completed: {success_count}/{total_users} users received the message")
        return success_count, total_users
    
    def check_stock(self, manual_check=False, chat_id=None):
        """Check if stock has significantly increased"""
        print("🔍 Checking Shein for stock updates...")
        
        current_stock, men_count, women_count = self.get_shein_stock_count()
        if current_stock == 0 and men_count == 0:
            error_msg = "❌ Could not retrieve stock count"
            print(error_msg)
            if manual_check and chat_id:
                asyncio.run(self.send_telegram_message(error_msg, chat_id))
            return
        
        previous_stock, prev_men_count, prev_women_count = self.get_previous_stock()
        men_change = men_count - prev_men_count
        women_change = women_count - prev_women_count
        
        print(f"📊 Men's Stock: {men_count} (Previous: {prev_men_count}, Change: {men_change})")
        print(f"👚 Women's Stock: {women_count} (Previous: {prev_women_count}, Change: {women_change})")
        
        if manual_check and chat_id:
            status_message = f"""
📊 CURRENT STOCK STATUS:

👕 Men's Items: {men_count}
👚 Women's Items: {women_count}
🔄 Total Items: {current_stock}

📈 Change from last check:
   • Men: {men_change}
   • Women: {women_change}

⏰ Last Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

🔗 {self.config['api_url']}
            """.strip()
            asyncio.run(self.send_telegram_message(status_message, chat_id))
            # Save current stock for manual checks too
            self.save_current_stock(current_stock, men_count, women_count, men_change)
            return
        
        # Check for significant men's stock increase (at least 2 items as requested)
        men_stock_increased = (
            men_change >= self.config['min_increase_threshold_men'] and 
            men_count >= self.config['min_stock_threshold'] and
            not self.has_stock_been_notified(men_count, "men_stock")
        )
        
        # Check for significant women's stock increase
        women_stock_increased = (
            women_change >= self.config['min_increase_threshold_women'] and 
            not self.has_stock_been_notified(women_count, "women_stock")
        )
        
        if men_stock_increased:
            print(f"🚨 Men's stock significantly increased: +{men_change}")
            self.save_current_stock(current_stock, men_count, women_count, men_change, True)
            self.record_notification(men_count, "men_stock")
            asyncio.run(self.send_men_stock_alert_to_all(men_count, prev_men_count, men_change))
        
        elif women_stock_increased:
            print(f"🚨 Women's stock significantly increased: +{women_change}")
            self.save_current_stock(current_stock, men_count, women_count, women_change, True)
            self.record_notification(women_count, "women_stock")
            asyncio.run(self.send_women_stock_alert_to_all(women_count, prev_women_count, women_change))
        
        else:
            # Save current stock without notification
            self.save_current_stock(current_stock, men_count, women_count, men_change, False)
            if not manual_check:
                print("✅ No significant stock change detected or already notified")
    
    async def send_men_stock_alert_to_all(self, current_men_count, previous_men_count, increase):
        """Send MEN'S stock alert notifications to ALL users"""
        message = f"""
🚨 MEN'S SVerse STOCK ALERT! 🚨

👕 **Men's Stock Increased!**

📈 Change: +{increase} items
📊 Current Men's: {current_men_count} items
📉 Previous Men's: {previous_men_count} items

🔗 Check Now: {self.config['api_url']}

⏰ Alert Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

⚡ Quick! New Men's SVerse items available!
        """.strip()
        
        success_count, total_users = await self.broadcast_message(message)
        
        admin_report = f"""
📊 MEN'S STOCK ALERT REPORT

✅ Alert sent successfully!
👥 Recipients: {success_count}/{total_users} users
📈 Men's Stock Increase: +{increase}
🕒 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        """.strip()
        
        await self.send_telegram_message(admin_report, self.config['telegram_chat_id'])
    
    async def send_women_stock_alert_to_all(self, current_women_count, previous_women_count, increase):
        """Send WOMEN'S stock alert notifications to ALL users"""
        message = f"""
🚨 WOMEN'S SVerse STOCK ALERT! 🚨

👚 **Women's Stock Increased Significantly!**

📈 Change: +{increase} items
📊 Current Women's: {current_women_count} items
📉 Previous Women's: {previous_women_count} items

🔗 Check Now: {self.config['api_url']}

⏰ Alert Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

⚡ Quick! New Women's SVerse items available!
        """.strip()
        
        success_count, total_users = await self.broadcast_message(message)
        
        admin_report = f"""
📊 WOMEN'S STOCK ALERT REPORT

✅ Alert sent successfully!
👥 Recipients: {success_count}/{total_users} users
📈 Women's Stock Increase: +{increase}
🕒 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        """.strip()
        
        await self.send_telegram_message(admin_report, self.config['telegram_chat_id'])
    
    async def send_test_notification(self, chat_id=None):
        """Send a test notification to verify everything works"""
        test_message = f"""
🧪 TEST NOTIFICATION - Shein Stock Monitor

✅ Your Shein stock monitor is working correctly!
🤖 Bot is active and ready to send alerts
📱 You will receive notifications when SVerse stock increases

🔗 Monitoring: {self.config['api_url']}

⏰ Test Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

🎉 Everything is set up properly!
        """.strip()
        
        if chat_id:
            await self.send_telegram_message(test_message, chat_id)
        else:
            await self.broadcast_message(test_message)
        
        print("✅ Test notification sent successfully!")
    
    def start_monitoring_loop(self):
        """Start monitoring in background thread"""
        def monitor():
            print("🔄 Monitoring loop started!")
            while self.monitoring:
                self.check_stock()
                time.sleep(self.config['check_interval_seconds'])
            print("🛑 Monitoring loop stopped")
        
        self.monitor_thread = threading.Thread(target=monitor)
        self.monitor_thread.daemon = True
        self.monitor_thread.start()

    def start_monitoring(self):
        """Start the monitoring"""
        if self.monitoring:
            print("🔄 Monitoring is already running!")
            return
        
        self.monitoring = True
        self.start_monitoring_loop()
        asyncio.run(self.send_test_notification())
        self.check_stock()
        print("✅ Monitor started successfully! Running 24/7...")
    
    def stop_monitoring(self):
        """Stop monitoring"""
        if not self.monitoring:
            print("❌ Monitoring is not running!")
            return
        
        self.monitoring = False
        print("🛑 Monitoring stopped!")

    async def handle_telegram_command(self, command, chat_id, user_id):
        """Handle Telegram commands using direct API calls"""
        try:
            is_admin_user = self.is_admin(user_id)
            
            user_info = await self.get_user_info(user_id)
            if user_info:
                self.add_user(
                    user_id=user_id,
                    username=user_info.get('username', ''),
                    first_name=user_info.get('first_name', ''),
                    last_name=user_info.get('last_name', ''),
                    chat_id=chat_id
                )
            
            if command == '/start' or command == '/help':
                user_count = self.get_user_count()
                if is_admin_user:
                    welcome_message = f"""
🤖 Welcome to Shein Stock Monitor - ADMIN MODE

You have administrator privileges.

Available Commands:
• /start_monitor - Start automatic monitoring (Admin only)
• /stop_monitor - Stop monitoring (Admin only)
• /check_now - Check stock immediately
• /status - Current monitor status
• /admin - Admin information
• /users - User statistics

👥 Total Users: {user_count}

Use the buttons below to control the monitor!
                    """.strip()
                else:
                    welcome_message = f"""
🤖 Welcome to Shein Stock Monitor!

I will monitor SVerse stock and alert you when new items are added.

Available Commands:
• /check_now - Check stock immediately
• /status - Current monitor status

👥 Total Users: {user_count}

Use the buttons below to interact with the monitor!
                    """.strip()
                
                await self.send_telegram_message_with_keyboard(welcome_message, chat_id, is_admin_user)
            
            elif command == '/start_monitor':
                if not is_admin_user:
                    await self.send_telegram_message("❌ Access Denied! Only administrators can start monitoring.", chat_id)
                    return
                
                if self.monitoring:
                    await self.send_telegram_message("🔄 Monitoring is already running!", chat_id)
                else:
                    self.monitoring = True
                    self.start_monitoring_loop()
                    user_count = self.get_user_count()
                    await self.send_telegram_message_with_keyboard(
                        f"✅ Shein Stock Monitor STARTED! Bot is now actively monitoring SVerse stock for {user_count} users.", 
                        chat_id, 
                        is_admin_user
                    )
                    await self.send_test_notification(chat_id)
                    print("✅ Monitor started via admin command!")
            
            elif command == '/stop_monitor':
                if not is_admin_user:
                    await self.send_telegram_message("❌ Access Denied! Only administrators can stop monitoring.", chat_id)
                    return
                
                if not self.monitoring:
                    await self.send_telegram_message("❌ Monitoring is not running!", chat_id)
                else:
                    self.monitoring = False
                    await self.send_telegram_message("🛑 Monitoring stopped!", chat_id)
                    print("🛑 Monitoring stopped via admin command!")
            
            elif command == '/check_now':
                await self.send_telegram_message("🔍 Checking stock immediately...", chat_id)
                print("🔍 Manual stock check requested")
                self.check_stock(manual_check=True, chat_id=chat_id)
            
            elif command == '/status':
                status = "🟢 RUNNING" if self.monitoring else "🔴 STOPPED"
                user_count = self.get_user_count()
                
                cursor = self.conn.cursor()
                cursor.execute('SELECT total_stock, men_count, women_count, timestamp FROM stock_history ORDER BY timestamp DESC LIMIT 1')
                result = cursor.fetchone()
                
                if result:
                    total_stock, men_count, women_count, last_check = result
                    status_message = f"""
🤖 SHEIN STOCK MONITOR STATUS

📊 Monitor Status: {status}
👥 Total Users: {user_count}
⏰ Last Check: {last_check}
🔄 Check Interval: 2 seconds

📈 Latest Stock Data:
   • Men's Items: {men_count}
   • Women's Items: {women_count}
   • Total Items: {total_stock}

🔗 Monitoring: {self.config['api_url']}
                    """.strip()
                else:
                    status_message = f"""
🤖 SHEIN STOCK MONITOR STATUS

📊 Monitor Status: {status}
👥 Total Users: {user_count}
⏰ Last Check: Never
🔄 Check Interval: 2 seconds

📈 No stock data collected yet.

🔗 Monitoring: {self.config['api_url']}
                    """.strip()
                
                await self.send_telegram_message(status_message, chat_id)
            
            elif command == '/admin':
                if not is_admin_user:
                    await self.send_telegram_message("❌ Access Denied! Admin command only.", chat_id)
                    return
                
                admin_count = len(self.config['admin_user_ids'])
                user_count = self.get_user_count()
                admin_info = f"""
👑 ADMIN INFORMATION

🤖 Bot Status: {'🟢 RUNNING' if self.monitoring else '🔴 STOPPED'}
👥 Total Users: {user_count}
👑 Admin Users: {admin_count}
📱 Your ID: {user_id}
⏰ Server Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

You have full control over the monitor.
                """.strip()
                await self.send_telegram_message(admin_info, chat_id)
            
            elif command == '/users':
                if not is_admin_user:
                    await self.send_telegram_message("❌ Access Denied! Admin command only.", chat_id)
                    return
                
                users = self.get_all_active_users()
                user_count = len(users)
                
                if user_count > 0:
                    user_list = "\n".join([f"• {user[2]} (@{user[1]}) - {user[0]}" for user in users[:10]])
                    if user_count > 10:
                        user_list += f"\n• ... and {user_count - 10} more users"
                    
                    users_message = f"""
👥 USER STATISTICS

📊 Total Users: {user_count}

👤 Recent Users:
{user_list}

⏰ Last Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                    """.strip()
                else:
                    users_message = "❌ No users found in the database."
                
                await self.send_telegram_message(users_message, chat_id)
            
            else:
                await self.send_telegram_message("❌ Unknown command. Use /start to see available commands.", chat_id)
                
        except Exception as e:
            print(f"❌ Error handling Telegram command: {e}")
            await self.send_telegram_message("❌ Error processing command. Please try again.", chat_id)
    
    async def get_user_info(self, user_id):
        """Get user info from Telegram"""
        try:
            url = f"https://api.telegram.org/bot{self.config['telegram_bot_token']}/getChat"
            payload = {
                'chat_id': user_id
            }
            response = requests.post(url, data=payload, timeout=10)
            if response.status_code == 200:
                return response.json().get('result', {})
        except Exception as e:
            print(f"⚠️ Error getting user info: {e}")
        return None

def ensure_polling_mode(token):
    """Ensure the bot is in polling mode and prevent conflicts"""
    print("🔄 Ensuring bot is in polling mode...")
    
    # Method 1: Delete any existing webhook
    try:
        url = f"https://api.telegram.org/bot{token}/deleteWebhook"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            result = response.json()
            if result.get('ok'):
                print("✅ Webhook deleted successfully")
            else:
                print(f"ℹ️ Webhook delete result: {result.get('description')}")
    except Exception as e:
        print(f"⚠️ Error deleting webhook: {e}")
    
    # Method 2: Set empty webhook URL
    try:
        url = f"https://api.telegram.org/bot{token}/setWebhook"
        payload = {'url': ''}
        response = requests.post(url, data=payload, timeout=10)
        if response.status_code == 200:
            result = response.json()
            if result.get('ok'):
                print("✅ Empty webhook set successfully")
            else:
                print(f"ℹ️ Empty webhook result: {result.get('description')}")
    except Exception as e:
        print(f"⚠️ Error setting empty webhook: {e}")
    
    # Method 3: Get webhook info to confirm
    try:
        url = f"https://api.telegram.org/bot{token}/getWebhookInfo"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            result = response.json()
            if result.get('ok'):
                webhook_info = result.get('result', {})
                if not webhook_info.get('url'):
                    print("✅ Confirmed: No active webhook (polling mode ready)")
                else:
                    print(f"⚠️ Webhook still active: {webhook_info.get('url')}")
    except Exception as e:
        print(f"⚠️ Error getting webhook info: {e}")
    
    print("✅ Bot is ready for polling mode")

def check_bot_health(token):
    """Check if bot is healthy and ready"""
    try:
        url = f"https://api.telegram.org/bot{token}/getMe"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get('ok'):
                bot_info = data.get('result', {})
                print(f"✅ Bot is healthy: @{bot_info.get('username', 'Unknown')}")
                return True
        print(f"❌ Bot health check failed: {response.status_code}")
        return False
    except Exception as e:
        print(f"❌ Bot health check error: {e}")
        return False

def start_conflict_free_telegram_bot(monitor):
    """Start a conflict-free Telegram bot using proper polling"""
    def poll_telegram_updates():
        print("🤖 Starting conflict-free Telegram bot polling...")
        
        # Step 1: Health check
        if not check_bot_health(CONFIG['telegram_bot_token']):
            print("❌ Bot health check failed, cannot start Telegram bot")
            return
        
        # Step 2: Ensure polling mode
        ensure_polling_mode(CONFIG['telegram_bot_token'])
        
        last_update_id = 0
        error_count = 0
        max_errors = 10
        
        while True:
            try:
                # Get updates from Telegram with NO timeout (short polling)
                url = f"https://api.telegram.org/bot{CONFIG['telegram_bot_token']}/getUpdates"
                params = {
                    'offset': last_update_id + 1,
                    'timeout': 0,  # No long polling - prevents conflicts
                    'allowed_updates': ['message']
                }
                
                response = requests.get(url, params=params, timeout=10)
                
                # If we get a conflict, it means someone else is using webhooks
                if response.status_code == 409:
                    print("❌ CONFLICT DETECTED: Another service is using webhooks with this bot token!")
                    print("💡 Solution: Stop any other services using this bot token")
                    print("🔄 This bot will continue monitoring but Telegram commands may not work")
                    time.sleep(30)  # Wait before retrying
                    continue
                
                response.raise_for_status()
                error_count = 0  # Reset error count on success
                
                data = response.json()
                if data.get('ok') and data.get('result'):
                    for update in data['result']:
                        last_update_id = update['update_id']
                        
                        if 'message' in update and 'text' in update['message']:
                            message = update['message']
                            chat_id = message['chat']['id']
                            user_id = message['from']['id']
                            text = message['text']
                            
                            print(f"📱 Received command: {text} from user {user_id}")
                            asyncio.run(monitor.handle_telegram_command(text, chat_id, user_id))
                else:
                    # No new updates, sleep briefly to avoid rate limits
                    time.sleep(0.5)
                
            except requests.RequestException as e:
                error_count += 1
                print(f"⚠️ Telegram polling error ({error_count}/{max_errors}): {e}")
                
                if error_count >= max_errors:
                    print("🔧 Too many errors, waiting before continuing...")
                    time.sleep(30)
                    error_count = 0
                else:
                    time.sleep(2)
                
            except Exception as e:
                error_count += 1
                print(f"❌ Unexpected Telegram bot error ({error_count}/{max_errors}): {e}")
                
                if error_count >= max_errors:
                    print("🔧 Too many errors, waiting before continuing...")
                    time.sleep(30)
                    error_count = 0
                else:
                    time.sleep(2)
    
    bot_thread = threading.Thread(target=poll_telegram_updates)
    bot_thread.daemon = True
    bot_thread.start()
    print("✅ Conflict-free Telegram bot started successfully!")
    return True

def main():
    """Main function"""
    print("🚀 Starting Shein Stock Monitor Cloud Bot...")
    print("💡 This bot runs 24/7 in the cloud!")
    print("📱 Sends Telegram alerts when stock increases")
    admin_count = len(CONFIG['admin_user_ids'])
    print(f"👑 Admin users: {admin_count}")
    
    monitor = SheinStockMonitor(CONFIG)
    
    # Start conflict-free Telegram bot
    telegram_started = start_conflict_free_telegram_bot(monitor)
    
    # Start monitoring immediately
    print("🤖 Starting automatic monitoring...")
    monitor.start_monitoring()
    
    if telegram_started:
        print("✅ Monitor is running with Telegram commands!")
        print("💡 Use /start in Telegram to control the monitor.")
    else:
        print("✅ Monitor is running in monitoring-only mode!")
        print("💡 Stock alerts will still be sent to Telegram.")
    
    print("🤖 Bot is running 24/7...")
    
    try:
        # Keep the main thread alive
        while True:
            time.sleep(60)
            
    except KeyboardInterrupt:
        print("\n🛑 Stopping monitor...")
        monitor.stop_monitoring()

if __name__ == "__main__":
    main()
