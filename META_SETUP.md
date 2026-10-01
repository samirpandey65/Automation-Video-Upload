# Meta Facebook + Instagram Connection

1. Create/manage your Meta app: https://developers.facebook.com/apps
2. In App Settings → Basic, copy App ID and App Secret.
3. Create a Facebook Login for Business configuration using your existing configuration ID.
4. Required configuration permissions for this uploader are the permissions configured in Meta for Page publishing and Instagram content publishing.
5. In the uploader, open Connections → + Connect Meta Account.
6. Enter the Meta App ID, App Secret, and Configuration ID.
7. Use this OAuth redirect URI exactly:
   `http://localhost:8080/api/meta/oauth/callback`
8. Complete Meta authorization, select the Facebook Page, and save the connection.

The connection is stored locally by the uploader. The App Secret is entered locally and is not included in this package.


## Security / credential handling
- The distributable package does not contain a Meta App Secret.
- Enter the current App Secret in Connections when starting Meta OAuth.
- The OAuth exchange uses the exact locally entered secret and normalizes accidental zero-width/BOM copy characters.
