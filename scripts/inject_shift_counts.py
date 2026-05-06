path = r'd:\PROJECTS\New_PoS\apps\core\views.py'
with open(path, 'r', newline='') as f:
    content = f.read()

OLD = (
    "                context['today_dispatched_value'] = today_dispatched_qs.aggregate(t=Sum('total'))['t'] or 0\r\n"
)
NEW = (
    "                context['today_dispatched_value'] = today_dispatched_qs.aggregate(t=Sum('total'))['t'] or 0\r\n"
    "\r\n"
    "                # Shift counts for today (scoped to manager shop)\r\n"
    "                from apps.sales.models import Shift as _Shift\r\n"
    "                shift_today_qs = _Shift.objects.filter(\r\n"
    "                    tenant=user.tenant,\r\n"
    "                    start_time__date=today,\r\n"
    "                )\r\n"
    "                if user.location:\r\n"
    "                    shift_today_qs = shift_today_qs.filter(shop=user.location)\r\n"
    "                context['today_open_shifts'] = shift_today_qs.filter(status='OPEN').count()\r\n"
    "                context['today_closed_shifts'] = shift_today_qs.filter(status='CLOSED').count()\r\n"
)

if OLD in content:
    content = content.replace(OLD, NEW, 1)
    with open(path, 'w', newline='') as f:
        f.write(content)
    print("Done - shift counts inserted.")
else:
    print("ERROR: snippet not found.")
    # Show what's around the area
    idx = content.find("today_dispatched_value")
    print(repr(content[idx:idx+200]))
