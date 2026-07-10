const puppeteer = require('puppeteer');

(async () => {
  const browser = await puppeteer.launch({ headless: "new", args: ['--no-sandbox'] });
  const page = await browser.newPage();
  
  page.on('console', msg => console.log('PAGE LOG:', msg.text()));
  page.on('pageerror', error => console.log('PAGE ERROR:', error.message));

  await page.goto('http://127.0.0.1:8000/login/');
  await page.type('input[name=username]', 'manager@stores.com');
  await page.type('input[name=password]', 'demo123');
  await page.click('button[type="submit"]');
  await page.waitForNavigation();

  console.log('Logged in. Current URL:', page.url());

  // Wait for dashboard to load
  await page.waitForSelector('.dashboard-partial-dispatch-btn', { timeout: 5000 }).catch(() => console.log("Button not found!"));
  
  console.log("Clicking partial dispatch button...");
  await page.evaluate(() => {
    const btn = document.querySelector('.dashboard-partial-dispatch-btn');
    if (btn) btn.click();
    else console.log("Button is missing in DOM");
  });

  await new Promise(r => setTimeout(r, 2000));
  
  // Also check sale_list.html
  console.log("\nGoing to sale list...");
  await page.goto('http://127.0.0.1:8000/sales/?dispatch_status=pending');
  await page.waitForSelector('.inline-partial-dispatch-btn', { timeout: 5000 }).catch(() => console.log("Button not found on sale list!"));
  
  console.log("Clicking inline partial dispatch...");
  await page.evaluate(() => {
    const btn = document.querySelector('.inline-partial-dispatch-btn');
    if (btn) btn.click();
    else console.log("Button is missing in DOM");
  });

  await new Promise(r => setTimeout(r, 2000));

  await browser.close();
})();
