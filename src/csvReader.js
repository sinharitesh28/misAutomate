const fs = require('fs');
const csv = require('csv-parser');

/**
 * Reads and parses the student attendance data from a CSV file.
 * @param {string} filePath - Path to the CSV file.
 * @returns {Promise<Array<Object>>} - Array of student records.
 */
function readStudentsData(filePath) {
    return new Promise((resolve, reject) => {
        const results = [];
        if (!fs.existsSync(filePath)) {
            return reject(new Error(`CSV file not found at path: ${filePath}`));
        }
        
        fs.createReadStream(filePath)
            .pipe(csv())
            .on('data', (data) => results.push(data))
            .on('end', () => {
                resolve(results);
            })
            .on('error', (err) => reject(err));
    });
}

module.exports = { readStudentsData };
