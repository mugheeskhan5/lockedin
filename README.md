# DVWA Writeups

A module-by-module writeup series for the Damn Vulnerable Web Application (DVWA). Each writeup covers Low, Medium, and High security levels — explaining not just the payloads but why each level fails and what a real fix looks like.

---

## Modules

| # | Module | Status |
|---|--------|--------|
| 01 | [SQL Injection](sql-injection/writeup.md) | ✅ Done |
| 02 | SQL Injection (Blind) | 🔄 Coming Soon |
| 03 | XSS (Reflected) | 🔄 Coming Soon |
| 04 | XSS (Stored) | 🔄 Coming Soon |
| 05 | Command Injection | 🔄 Coming Soon |
| 06 | File Upload | 🔄 Coming Soon |
| 07 | CSRF | 🔄 Coming Soon |
| 08 | Brute Force | 🔄 Coming Soon |
| 09 | File Inclusion | 🔄 Coming Soon |
| 10 | Weak Session IDs | 🔄 Coming Soon |

---

## What Makes These Writeups Different

Most DVWA guides stop at "here is the payload." These writeups go deeper:

- **Source code analysis** at every security level — what the code does wrong and why
- **Why each bypass works** — not just the payload but the underlying mechanism
- **Developer fix section** in every module — the correct code, not just the concept
- **CWE classification** mapping each vulnerability to its industry-standard identifier

---

## Setup

These writeups assume you have DVWA running locally via Docker:

```bash
docker pull vulnerables/web-dvwa
docker run -d -p 80:80 vulnerables/web-dvwa
```

Then navigate to `http://localhost` and set the security level under DVWA Security.

---

## Author

**Muhammad Mughees Khan**  
Cybersecurity undergraduate at FAST-NUCES, Karachi  
[GitHub](https://github.com/mugheeskhan5) • [LinkedIn](https://linkedin.com/in/your-linkedin)

---

*More modules added weekly.*
