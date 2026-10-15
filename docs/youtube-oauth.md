# Connect your YouTube channel (one-time Google Cloud setup)

"Sync data from YT" signs in to your own channel with Google OAuth and pulls what only
the owner can see: YouTube Analytics (watch time, retention curves, traffic sources,
demographics) plus private and unlisted uploads. It needs an OAuth client in the same
Google Cloud project as your `YOUTUBE_API_KEY`. This takes about 5 minutes.

## 1. Enable the YouTube Analytics API

Google Cloud console → **APIs & Services → Library** → search **YouTube Analytics API**
→ **Enable**. (The YouTube Data API v3 is already enabled for your API key.)

## 2. Configure the OAuth consent screen

**APIs & Services → OAuth consent screen** (called **Google Auth Platform → Branding /
Audience / Data access** in the newer console):

1. User type: **External**. App name: `creator-lab`. Support and developer email: yours.
2. **Data access → Add or remove scopes**, add both:
   - `https://www.googleapis.com/auth/youtube.readonly`
   - `https://www.googleapis.com/auth/yt-analytics.readonly`
3. **Audience**: leave the publishing status on **Testing**, and under **Test users**
   add the Google account that owns @evanwitulski (the one you sign in to YouTube with).

Testing mode is fine for personal use and skips Google's verification. The one
catch: refresh tokens in Testing mode expire after 7 days, so you'll have to sign in
again at the next sync after that. The app tells you when.

## 3. Create the OAuth client

**APIs & Services → Credentials → Create credentials → OAuth client ID**:

- Application type: **Web application**
- Name: `creator-lab (local)`
- Authorized JavaScript origins: `http://localhost:5050`
- **Authorized redirect URIs**, exactly:

  ```
  http://localhost:5050/api/youtube/oauth/callback
  ```

Click **Create** and copy the **Client ID** and **Client secret**.

## 4. Put them in backend/.env

Open `D:\VideoAnalysis\creator-lab\backend\.env` and fill in these three lines:

```
GOOGLE_OAUTH_CLIENT_ID=...apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=...
GOOGLE_OAUTH_REDIRECT_URI=http://localhost:5050/api/youtube/oauth/callback
```

`backend/.env` is gitignored, so never commit it. Restart the server afterwards.

## 5. Sign in

Open the app at **http://localhost:5050** (use `localhost` rather than `127.0.0.1`, so
the browser returns to the same address Google redirects to). Go to **Settings →
YouTube owner sync → Sign in with YouTube**, pick your account and allow read-only
access.

The refresh token is stored encrypted in the database (with a key derived from
`SECRET_KEY`). It never appears in logs or the repo. To disconnect, use **Settings →
Disconnect**, and you can also revoke access at https://myaccount.google.com/permissions.

## Quota

Analytics reports don't count against the Data API quota. Listing your uploads and
their details uses the normal Data API quota (about 2 units per 50 videos).
