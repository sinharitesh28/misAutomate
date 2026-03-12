const imap = require('imap-simple');
const simpleParser = require('mailparser').simpleParser;

/**
 * Connects to Gmail via IMAP and waits for the latest email containing the OTP.
 * @returns {Promise<string>} The extracted OTP.
 */
async function getOTP() {
    const config = {
        imap: {
            user: process.env.EMAIL_USER,
            password: process.env.EMAIL_PASS,
            host: 'imap.gmail.com',
            port: 993,
            tls: true,
            authTimeout: 30000,
            tlsOptions: { rejectUnauthorized: false }
        }
    };

    console.log('Connecting to Gmail to fetch OTP...');

    return new Promise((resolve, reject) => {
        imap.connect(config).then(connection => {
            return connection.openBox('INBOX').then(() => {
                // Search for unseen emails from the portal sender.
                const searchCriteria = ['UNSEEN', ['FROM', 'no-reply@pumis.in']];
                const fetchOptions = {
                    bodies: ['HEADER', 'TEXT'],
                    markSeen: true,
                    struct: true
                };

                // Poll for new emails until we find the OTP.
                let attempts = 0;
                const maxAttempts = 15; // 15 attempts * 4s = 60s
                const intervalId = setInterval(async () => {
                    attempts++;
                    try {
                        const messages = await connection.search(searchCriteria, fetchOptions);

                        // Process messages from newest to oldest
                        for (let i = messages.length - 1; i >= 0; i--) {
                            const item = messages[i];
                            const all = item.parts.filter((part) => part.which === 'TEXT');
                            const html = all[0].body; // Depending on formatting, might need to parse via mailparser

                            const parsed = await simpleParser(html);
                            const text = parsed.text || parsed.html || String(html);

                            // Extract OTP that appears before "is your one time password" or just any 5 to 8 digit number
                            const otpMatch = text.match(/\b\d{5,8}\b/);
                            if (otpMatch) {
                                clearInterval(intervalId);
                                connection.end();
                                resolve(otpMatch[0]);
                                return;
                            }
                        }

                        if (attempts >= maxAttempts) {
                            clearInterval(intervalId);
                            connection.end();
                            reject(new Error('Timeout: Could not find OTP in recent emails.'));
                        }
                    } catch (err) {
                        clearInterval(intervalId);
                        connection.end();
                        reject(err);
                    }
                }, 4000); // Poll every 4 seconds
            });
        }).catch(err => {
            reject(err);
        });
    });
}

module.exports = { getOTP };
