from __future__ import annotations

import sys
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path


output = Path(sys.argv[1])
message = EmailMessage(policy=SMTP)
message["From"] = "{{operations_manager_email}}"
message["To"] = "{{analyst_email}}, {{counterparty_email}}"
message["Cc"] = "{{review_owner_email}}"
message["Date"] = "Thu, 01 Jan 1970 00:00:00 +0000"
message["Message-ID"] = "<build-slot-message-id@template.invalid>"
message["Subject"] = "{{case_reference}} | corrected support and remaining item"
message["X-Syn-Studios-Template"] = "TMPL-0003@1.0.0"
message["X-Syn-Build-Slots"] = "date,message-id,participants,case-reference,due-date,attachment-facts"

message.set_content("""{{operations_manager_first_name}},

The corrected support is attached. Please use {{corrected_attachment_name}} and disregard {{superseded_attachment_name}}.

The remaining item is {{open_item_description}}. {{open_item_owner_role}} owns the follow-up by {{open_item_due_date}}. This message coordinates the work; it does not approve or resolve the underlying treatment.

Thanks,
{{sender_display_name}}
{{sender_role}}

-----Original Message-----
From: {{counterparty_display_name}} <{{counterparty_email}}>
Sent: {{message_3_timestamp}}
To: {{operations_manager_display_name}} <{{operations_manager_email}}>
Subject: RE: {{case_reference}} | support request

We can confirm receipt of {{received_item_description}}. The field labeled {{questioned_field_name}} still needs clarification from your team.

-----Original Message-----
From: {{analyst_display_name}} <{{analyst_email}}>
Sent: {{message_2_timestamp}}
To: {{counterparty_display_name}} <{{counterparty_email}}>
Cc: {{operations_manager_display_name}} <{{operations_manager_email}}>
Subject: RE: {{case_reference}} | support request

Attaching the first export now. I have not validated {{unvalidated_scope}} yet; I will follow up after the source owner responds.

-----Original Message-----
From: {{operations_manager_display_name}} <{{operations_manager_email}}>
Sent: {{message_1_timestamp}}
To: {{analyst_display_name}} <{{analyst_email}}>
Subject: {{case_reference}} | support request

Please assemble the source support for {{request_scope}}. Keep any unresolved questions separate from the governing record.
""")

message.add_attachment(
    "row_reference,status_code,question_owner_role\n{{row_reference}},{{status_code}},{{question_owner_role}}\n",
    subtype="csv",
    filename="{{corrected_attachment_name}}",
)
message.add_attachment(
    "Correction note: {{correction_summary}}\nSuperseded attachment: {{superseded_attachment_name}}\n",
    subtype="plain",
    filename="{{correction_note_name}}",
)
message.set_boundary("syn-studios-tmpl-0003-1-0-0")

output.parent.mkdir(parents=True, exist_ok=True)
output.write_bytes(message.as_bytes())
